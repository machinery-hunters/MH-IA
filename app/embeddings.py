"""
embeddings

nomic-embed-text (y en general la familia de modelos Nomic) requiere
anteponer un prefijo de tarea al texto para producir embeddings de buena
calidad:
  - "search_document: " para el contenido que se indexa
  - "search_query: "    para el texto de la consulta de búsqueda

Sin este prefijo, el modelo sigue generando vectores, pero la separación
semántica entre fragmentos relevantes e irrelevantes se degrada de forma
notable — es la causa más común de que la recuperación "no encuentre nada"
con este modelo aunque el documento correcto sí esté indexado.

Referencia: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5

Este wrapper aplica el prefijo automáticamente y de forma transparente para
el resto del código (indexer, retriever_node) — ambos deben usar la MISMA
clase, porque el prefijo usado al indexar y al consultar debe ser
consistente con el propósito de cada texto (documento vs. consulta).
"""

from langchain_ollama import OllamaEmbeddings


class TaskPrefixedOllamaEmbeddings(OllamaEmbeddings):
    def _needs_nomic_prefix(self) -> bool:
        return "nomic-embed" in self.model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._needs_nomic_prefix():
            texts = [f"search_document: {t}" for t in texts]
        return super().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if self._needs_nomic_prefix():
            text = f"search_query: {text}"
        return super().embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._needs_nomic_prefix():
            texts = [f"search_document: {t}" for t in texts]
        return await super().aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        if self._needs_nomic_prefix():
            text = f"search_query: {text}"
        return await super().aembed_query(text)