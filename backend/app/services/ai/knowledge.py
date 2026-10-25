"""RAG 知识库服务 —— 文档解析、分块、向量化、混合检索"""

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.embedding import get_embedding, get_embeddings_batch
from app.services.ai.qdrant import (
    COLLECTION_KNOWLEDGE,
    search_similar,
    upsert_vectors,
)

logger = logging.getLogger(__name__)

# 分块参数
CHUNK_SIZE = 400  # 目标字数
CHUNK_OVERLAP = 50  # 重叠字数

# 上传目录
UPLOAD_DIR = Path("data/ai-knowledge")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}


async def save_uploaded_file(
    user_id: str,
    filename: str,
    content: bytes,
) -> str:
    """保存上传文件到磁盘，返回文件路径"""
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid4())[:8]
    ext = Path(filename).suffix
    safe_name = f"{file_id}_{filename}"
    file_path = user_dir / safe_name

    with open(file_path, "wb") as f:
        f.write(content)

    return str(file_path)


async def parse_document(file_path: str, filename: str) -> str:
    """解析文档，提取纯文本"""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return await _parse_pdf(file_path)
    elif ext in (".txt", ".md"):
        return _parse_text(file_path)
    elif ext == ".csv":
        return _parse_csv(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


async def _parse_pdf(file_path: str) -> str:
    """解析 PDF 文件"""
    try:
        import pymupdf  # type: ignore
        doc = pymupdf.open(file_path)
        text_parts: list[str] = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(f"[第{page_num + 1}页]\n{text}")
        doc.close()
        return "\n\n".join(text_parts)
    except ImportError:
        # pymupdf 未安装，降级为提示
        logger.warning("pymupdf 未安装，无法解析 PDF")
        return "[PDF 解析需要安装 pymupdf]"


def _parse_text(file_path: str) -> str:
    """解析 TXT/Markdown 文件"""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _parse_csv(file_path: str) -> str:
    """解析 CSV 文件"""
    import csv
    rows: list[str] = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(" | ".join(row))
    return "\n".join(rows)


def chunk_text(text: str, document_name: str) -> list[dict]:
    """将文本分块（300-500 字/块，重叠 50 字）"""
    if not text.strip():
        return []

    chunks: list[dict] = []
    # 按段落/换行符分块
    paragraphs = text.split("\n\n")
    current_chunk = ""
    chunk_index = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) > CHUNK_SIZE:
            # 当前块已满，保存
            if current_chunk:
                chunks.append({
                    "text": current_chunk.strip(),
                    "document_name": document_name,
                    "chunk_index": chunk_index,
                })
                chunk_index += 1
                # 保留重叠部分
                overlap_text = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else current_chunk
                current_chunk = overlap_text + "\n\n" + para
            else:
                # 单个段落就超过大小，直接作为一个块
                chunks.append({
                    "text": para[:CHUNK_SIZE + CHUNK_OVERLAP],
                    "document_name": document_name,
                    "chunk_index": chunk_index,
                })
                chunk_index += 1
                current_chunk = ""
        else:
            current_chunk += "\n\n" + para if current_chunk else para

    # 最后一个块
    if current_chunk.strip():
        chunks.append({
            "text": current_chunk.strip(),
            "document_name": document_name,
            "chunk_index": chunk_index,
        })

    return chunks


async def vectorize_and_store(
    session: AsyncSession,
    user_id: str,
    chunks: list[dict],
    document_id: str,
) -> int:
    """将文本块向量化并存入 Qdrant"""
    if not chunks:
        return 0

    texts = [c["text"] for c in chunks]
    vectors = await get_embeddings_batch(session, user_id, texts)

    points = []
    for chunk, vector in zip(chunks, vectors):
        points.append({
            "id": str(uuid4()),
            "vector": vector,
            "payload": {
                "user_id": user_id,
                "document_id": document_id,
                "document_name": chunk["document_name"],
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"][:500],
            },
        })

    await upsert_vectors(COLLECTION_KNOWLEDGE, points)
    return len(points)


async def hybrid_search(
    session: AsyncSession,
    user_id: str,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """混合检索：向量检索 + 关键词检索，合并去重"""
    vector = await get_embedding(session, user_id, query)

    # 1. 向量相似度检索（取 top_k * 2 扩大候选集）
    vector_results = await search_similar(
        COLLECTION_KNOWLEDGE,
        vector,
        limit=top_k * 2,
        user_id_filter=user_id,
    )

    # 2. 关键词检索：在向量结果中按关键词匹配加权
    keywords = [w for w in query.split() if len(w) >= 2]
    keyword_hits: dict[str, float] = {}
    for kw in keywords:
        kw_lower = kw.lower()
        # 用 Qdrant payload 过滤做关键词匹配
        from qdrant_client.models import FieldCondition, MatchText
        from app.services.ai.qdrant import get_qdrant_client
        from qdrant_client.models import Filter
        client = await get_qdrant_client()
        kw_results = await client.scroll(
            collection_name=COLLECTION_KNOWLEDGE,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="user_id", match={"value": user_id}),
                    FieldCondition(key="text", match={"text": kw_lower}),
                ]
            ),
            limit=top_k,
            with_payload=True,
        )
        for point in kw_results[0]:
            pid = str(point.id)
            keyword_hits[pid] = keyword_hits.get(pid, 0) + 1.0

    # 3. 合并：向量结果 + 关键词加权
    merged: dict[str, dict] = {}
    for r in vector_results:
        payload = r.get("payload", {})
        pid = r.get("id", "")
        score = r.get("score", 0)
        # 关键词加权
        kw_boost = keyword_hits.get(pid, 0) * 0.1
        merged[pid] = {
            "text": payload.get("text", ""),
            "document_name": payload.get("document_name", "未知文档"),
            "chunk_index": payload.get("chunk_index", 0),
            "score": score + kw_boost,
        }

    # 4. 补充纯关键词命中但向量未召回的
    for pid, boost in keyword_hits.items():
        if pid not in merged:
            pass  # 需要额外查询 payload，已在向量召回范围内忽略

    # 5. 按综合分数排序，取 top_k
    sorted_results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    return sorted_results[:top_k]


async def list_documents(user_id: str) -> list[dict]:
    """列出用户上传的所有知识库文档（按 document_id 分组）"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    all_points: list[dict] = []
    offset = None

    # 分页 scroll 获取所有知识点
    while True:
        results, next_offset = await client.scroll(
            collection_name=COLLECTION_KNOWLEDGE,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=100,
            offset=offset,
            with_payload=True,
        )
        for point in results:
            payload = point.payload or {}
            all_points.append({
                "id": str(point.id),
                "document_id": payload.get("document_id", ""),
                "document_name": payload.get("document_name", "未知文档"),
                "chunk_index": payload.get("chunk_index", 0),
                "text_preview": (payload.get("text", ""))[:80],
            })
        if next_offset is None:
            break
        offset = next_offset

    # 按 document_id 分组
    docs: dict[str, dict] = {}
    for p in all_points:
        doc_id = p["document_id"]
        if doc_id not in docs:
            docs[doc_id] = {
                "document_id": doc_id,
                "document_name": p["document_name"],
                "chunk_count": 0,
                "point_ids": [],
            }
        docs[doc_id]["chunk_count"] += 1
        docs[doc_id]["point_ids"].append(p["id"])

    return list(docs.values())


async def delete_document(user_id: str, document_id: str) -> int:
    """删除指定文档的所有向量块"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    # 先查出所有属于该文档的 point ids
    results, _ = await client.scroll(
        collection_name=COLLECTION_KNOWLEDGE,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="document_id", match=MatchValue(value=document_id)),
            ]
        ),
        limit=500,
        with_payload=False,
    )

    if not results:
        return 0

    point_ids = [p.id for p in results]
    await client.delete(
        collection_name=COLLECTION_KNOWLEDGE,
        points_selector=point_ids,
    )
    return len(point_ids)


def format_context_from_results(results: list[dict]) -> str:
    """将检索结果格式化为注入 AI 上下文的文本"""
    if not results:
        return ""

    parts = ["[知识库检索结果]"]
    for i, r in enumerate(results, 1):
        source = f"{r['document_name']}"
        if r.get("chunk_index", 0) > 0:
            source += f" (片段 {r['chunk_index']})"
        parts.append(f"\n--- 来源: {source} ---\n{r['text']}")
    parts.append("\n[请基于以上知识库内容回答，并在回答中标注来源]")

    return "\n".join(parts)
