#!/usr/bin/env python3
"""
Fast PDF Reader MCP Server for Antigravity & LLM Agents.
Engine: PyMuPDF (C-speed) + pymupdf4llm (Markdown & table preservation).
Fast, low-token, precise on-demand page reading and search.
"""

import sys
import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List

import pymupdf
import pymupdf4llm

# Fix stdout encoding on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")

TOOLS = [
    {
        "name": "pdf_info",
        "description": "Returns PDF metadata: total page count, file size, title, author, and encryption status. Costs ~30 tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or workspace path to the PDF file"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pdf_toc",
        "description": "Returns the Table of Contents (bookmarks/outlines) of the PDF with title, page numbers, and hierarchy level. Costs ~100 tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or workspace path to the PDF file"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pdf_search",
        "description": "Performs ultra-fast full-text search across all PDF pages. Returns matching page numbers and contextual excerpts without loading entire document. Costs ~200 tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the PDF file"},
                "query": {"type": "string", "description": "Word or phrase to search for"},
                "max_results": {"type": "integer", "description": "Maximum number of occurrences to return (default 10)"},
                "case_sensitive": {"type": "boolean", "description": "Whether search is case-sensitive (default false)"}
            },
            "required": ["file_path", "query"]
        }
    },
    {
        "name": "pdf_read_page",
        "description": "Extracts clean Markdown or text from a single specific PDF page. Costs ~150-300 tokens instead of 20,000 for image.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the PDF file"},
                "page_number": {"type": "integer", "description": "1-based page number to read"},
                "as_markdown": {"type": "boolean", "description": "Format text and tables as Markdown (default true)"}
            },
            "required": ["file_path", "page_number"]
        }
    },
    {
        "name": "pdf_read_range",
        "description": "Extracts clean Markdown or text from a range of pages (inclusive). Costs proportional to requested pages only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the PDF file"},
                "start_page": {"type": "integer", "description": "1-based start page (inclusive)"},
                "end_page": {"type": "integer", "description": "1-based end page (inclusive)"},
                "as_markdown": {"type": "boolean", "description": "Format as Markdown (default true)"}
            },
            "required": ["file_path", "start_page", "end_page"]
        }
    }
]


def resolve_path(file_path: str) -> str:
    p = Path(file_path)
    if not p.is_absolute():
        p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"PDF file not found at: {file_path}")
    return str(p)


def handle_pdf_info(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = resolve_path(args["file_path"])
    doc = pymupdf.open(file_path)
    meta = doc.metadata or {}
    file_size_kb = round(os.path.getsize(file_path) / 1024, 1)
    
    return {
        "file_path": file_path,
        "page_count": len(doc),
        "file_size_kb": file_size_kb,
        "is_encrypted": doc.is_encrypted,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "subject": meta.get("subject", ""),
        "keywords": meta.get("keywords", "")
    }


def handle_pdf_toc(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = resolve_path(args["file_path"])
    doc = pymupdf.open(file_path)
    toc_raw = doc.get_toc()  # [[lvl, title, page], ...]
    
    toc = []
    for item in toc_raw:
        if len(item) >= 3:
            lvl, title, page = item[0], item[1], item[2]
            toc.append({
                "level": lvl,
                "title": title.strip(),
                "page": page
            })
            
    return {
        "file_path": file_path,
        "total_pages": len(doc),
        "toc_count": len(toc),
        "table_of_contents": toc if toc else "No embedded table of contents found in this PDF."
    }


def handle_pdf_search(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = resolve_path(args["file_path"])
    query = args["query"].strip()
    max_results = int(args.get("max_results", 10))
    case_sensitive = bool(args.get("case_sensitive", False))
    
    if not query:
        return {"error": "Query cannot be empty"}
        
    doc = pymupdf.open(file_path)
    results = []
    
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text()
        
        matches = list(pattern.finditer(text))
        for match in matches:
            start_pos = max(0, match.start() - 100)
            end_pos = min(len(text), match.end() + 100)
            snippet = text[start_pos:end_pos].replace("\n", " ").strip()
            
            results.append({
                "page": page_idx + 1,
                "snippet": f"...{snippet}..."
            })
            
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break
            
    return {
        "file_path": file_path,
        "query": query,
        "total_matches_found": len(results),
        "results": results
    }


def handle_pdf_read_page(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = resolve_path(args["file_path"])
    page_num = int(args["page_number"])
    as_markdown = bool(args.get("as_markdown", True))
    
    doc = pymupdf.open(file_path)
    if page_num < 1 or page_num > len(doc):
        return {"error": f"Invalid page_number {page_num}. Document has {len(doc)} pages (1..{len(doc)})."}
        
    page_idx = page_num - 1
    
    if as_markdown:
        # Use pymupdf4llm for markdown and tables
        try:
            content = pymupdf4llm.to_markdown(doc, pages=[page_idx])
        except Exception:
            content = doc[page_idx].get_text()
    else:
        content = doc[page_idx].get_text()
        
    return {
        "file_path": file_path,
        "page": page_num,
        "total_pages": len(doc),
        "content": content
    }


def handle_pdf_read_range(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = resolve_path(args["file_path"])
    start_page = int(args["start_page"])
    end_page = int(args["end_page"])
    as_markdown = bool(args.get("as_markdown", True))
    
    doc = pymupdf.open(file_path)
    total_pages = len(doc)
    
    if start_page < 1 or end_page > total_pages or start_page > end_page:
        return {"error": f"Invalid range [{start_page}..{end_page}]. Document has {total_pages} pages."}
        
    page_indices = list(range(start_page - 1, end_page))
    
    if as_markdown:
        try:
            content = pymupdf4llm.to_markdown(doc, pages=page_indices)
        except Exception:
            content = "\n\n--- PAGE BREAK ---\n\n".join([doc[i].get_text() for i in page_indices])
    else:
        content = "\n\n--- PAGE BREAK ---\n\n".join([doc[i].get_text() for i in page_indices])
        
    return {
        "file_path": file_path,
        "start_page": start_page,
        "end_page": end_page,
        "total_pages": total_pages,
        "content": content
    }


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": TOOLS}
                }
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments", {})

                if name == "pdf_info":
                    result = handle_pdf_info(args)
                elif name == "pdf_toc":
                    result = handle_pdf_toc(args)
                elif name == "pdf_search":
                    result = handle_pdf_search(args)
                elif name == "pdf_read_page":
                    result = handle_pdf_read_page(args)
                elif name == "pdf_read_range":
                    result = handle_pdf_read_range(args)
                else:
                    result = {"error": f"Unknown tool: {name}"}

                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, indent=2)
                            }
                        ]
                    }
                }
            elif method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "pdf-reader-mcp", "version": "1.0.0"}
                    }
                }
            elif method == "notifications/initialized":
                continue
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method {method} not found"}
                }

            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": req.get("id") if "req" in locals() and isinstance(req, dict) else None,
                "error": {"code": -32603, "message": str(e)}
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
