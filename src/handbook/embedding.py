"""
embedding.py — Abstract EmbeddingProvider interface + Google adapter + Mock.

Kiến trúc adapter cho phép thay đổi provider/model/dimension mà không sửa
dữ liệu gốc. Chỉ Google được kiểm thử thật trong v1.

QUAN TRỌNG: Model name thực tế phụ thuộc vào phiên bản google-genai SDK.
- Thử "models/text-embedding-004" trước (rộng nhất)
- Fallback sang "models/gemini-embedding-2" hoặc "gemini-embedding-exp-03-07"
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

# ─── Abstract Interface ────────────────────────────────────────────────────────


class EmbeddingProvider(ABC):
    """Interface chung cho mọi embedding provider."""

    provider_id: str   # ví dụ: "google", "mock"
    model_id: str      # ví dụ: "models/text-embedding-004"
    dimension: int     # kích thước vector, ví dụ: 768

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """
        Kiểm tra provider còn sống.
        Trả về: {"ok": bool, "detail": str}
        """
        ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Nhúng danh sách văn bản (task_type=RETRIEVAL_DOCUMENT).
        Trả về list[list[float]], mỗi phần tử là vector có độ dài self.dimension.
        """
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """
        Nhúng một câu truy vấn (task_type=RETRIEVAL_QUERY).
        Trả về vector float có độ dài self.dimension.
        """
        ...


# ─── Mock Provider ─────────────────────────────────────────────────────────────


class MockEmbeddingProvider(EmbeddingProvider):
    """
    Provider giả cho unit test — không gọi network.
    Mọi vector là [0.1] * 768.
    """

    provider_id = "mock"
    model_id = "mock-768"
    dimension = 768

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimension for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self.dimension

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "mock"}


# ─── Google Provider ───────────────────────────────────────────────────────────

# Thứ tự thử model name — model đầu tiên hoạt động được ghi vào ACTIVE_GOOGLE_MODEL
_GOOGLE_MODEL_CANDIDATES = [
    "models/text-embedding-004",
    "models/gemini-embedding-2",
    "gemini-embedding-2",
    "gemini-embedding-exp-03-07",
]

ACTIVE_GOOGLE_MODEL: str | None = None  # được set khi health() thành công lần đầu


class GoogleEmbeddingProvider(EmbeddingProvider):
    """
    Google Gemini Embedding adapter dùng google-genai==2.10.0.

    Đọc GOOGLE_API_KEY từ biến môi trường (KHÔNG hardcode).
    Model mặc định: thử từng candidate trong _GOOGLE_MODEL_CANDIDATES cho đến
    khi tìm được model hoạt động.

    Nếu nhận 401/403: raise RuntimeError để caller biết key không hợp lệ.
    """

    provider_id = "google"
    dimension = 768

    def __init__(self, model_id: str | None = None):
        global ACTIVE_GOOGLE_MODEL

        from google import genai  # type: ignore[import-untyped]

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY không có trong biến môi trường. "
                "Hãy set: $Env:GOOGLE_API_KEY = 'your-key'"
            )

        self._client = genai.Client(api_key=api_key)
        self._genai = genai

        if model_id is not None:
            self.model_id = model_id
        elif ACTIVE_GOOGLE_MODEL is not None:
            self.model_id = ACTIVE_GOOGLE_MODEL
        else:
            # Chưa biết model nào work — dùng candidate đầu tiên,
            # health() sẽ tự thử và cập nhật.
            self.model_id = _GOOGLE_MODEL_CANDIDATES[0]

    def _try_embed(self, text: str, task_type: str, model: str) -> list[float]:
        """Thử embed với một model cụ thể, raise nếu lỗi."""
        resp = self._client.models.embed_content(
            model=model,
            contents=text,
            config=self._genai.types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimension,
            ),
        )
        return list(resp.embeddings[0].values)

    def health(self) -> dict[str, Any]:
        """
        Kiểm tra key và thử từng model candidate.
        Cập nhật self.model_id và ACTIVE_GOOGLE_MODEL khi tìm được model hoạt động.
        """
        global ACTIVE_GOOGLE_MODEL

        candidates = (
            [self.model_id]
            if self.model_id not in _GOOGLE_MODEL_CANDIDATES
            else _GOOGLE_MODEL_CANDIDATES
        )

        last_error = ""
        for model in candidates:
            try:
                vec = self._try_embed("health_check", "RETRIEVAL_QUERY", model)
                self.model_id = model
                ACTIVE_GOOGLE_MODEL = model
                return {
                    "ok": True,
                    "detail": f"model={model} dim={len(vec)}",
                    "model_used": model,
                    "dimension": len(vec),
                }
            except Exception as e:
                err_str = str(e)
                last_error = err_str
                # Nếu 401/403 — key lỗi, không thử tiếp
                if "401" in err_str or "403" in err_str or "PERMISSION_DENIED" in err_str:
                    return {
                        "ok": False,
                        "detail": f"Lỗi xác thực (401/403): {err_str}. "
                                  "Vui lòng cung cấp GOOGLE_API_KEY hợp lệ.",
                        "auth_error": True,
                    }
                continue

        return {
            "ok": False,
            "detail": f"Không tìm được model hoạt động. Lỗi cuối: {last_error}",
            "tried_models": candidates,
        }

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Embed danh sách văn bản (RETRIEVAL_DOCUMENT).
        Gọi tuần tự để tránh rate-limit.
        """
        results = []
        for text in texts:
            vec = self._try_embed(text, "RETRIEVAL_DOCUMENT", self.model_id)
            results.append(vec)
        return results

    def embed_query(self, text: str) -> list[float]:
        """Embed một câu truy vấn (RETRIEVAL_QUERY)."""
        return self._try_embed(text, "RETRIEVAL_QUERY", self.model_id)


# ─── Factory ───────────────────────────────────────────────────────────────────


def get_provider(provider_id: str = "mock") -> EmbeddingProvider:
    """
    Factory helper để lấy provider theo tên.
    Dùng "google" khi cần embedding thật, "mock" khi test.
    """
    if provider_id == "google":
        return GoogleEmbeddingProvider()
    if provider_id == "mock":
        return MockEmbeddingProvider()
    raise ValueError(f"Provider không xác định: {provider_id!r}")
