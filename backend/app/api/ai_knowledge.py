"""AI 知识库 API —— 文档上传、列表、删除、向量化"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.services.ai.knowledge import (
    save_uploaded_file,
    parse_document,
    chunk_text,
    vectorize_and_store,
    list_documents,
    delete_document,
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
)

router = APIRouter(prefix="/api/ai/knowledge", tags=["AI 知识库"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """上传文档到知识库（支持 PDF/TXT/Markdown/CSV，限制 10MB）"""
    # 校验文件类型
    filename = file.filename or "unknown.txt"
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，仅支持 {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # 读取文件内容
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    user_id = str(current_user.id)

    # 保存文件
    file_path = await save_uploaded_file(user_id, filename, content)

    # 解析文档
    try:
        text = await parse_document(file_path, filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档解析失败: {str(e)}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="文档内容为空")

    # 分块
    document_id = str(uuid4())[:8]
    chunks = chunk_text(text, filename)

    # 向量化存储
    try:
        count = await vectorize_and_store(session, user_id, chunks, document_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"向量化失败: {str(e)}")

    return {
        "ok": True,
        "document_id": document_id,
        "filename": filename,
        "chunks": len(chunks),
        "vectorized": count,
    }


@router.get("/documents")
async def list_knowledge_documents(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """列出用户上传的所有知识库文档"""
    return await list_documents(str(current_user.id))


@router.delete("/documents/{document_id}")
async def delete_knowledge_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除指定知识库文档及其所有向量块"""
    count = await delete_document(str(current_user.id), document_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"ok": True, "deleted_chunks": count}
