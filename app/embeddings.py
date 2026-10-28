"""The ONE place that turns text into an embedding (a vector of numbers).

An embedding model reads text and returns a list of numbers that captures its
MEANING. Texts with similar meaning get vectors that are close together, which is
what lets Couchbase find "similar" documents instead of only matching words.

This project uses a small open-source model that runs locally in the container, so
it needs no account, no API key and no internet connection:

    sentence-transformers/all-MiniLM-L6-v2  ->  384 numbers per text

The model is baked into the Docker image at build time.

SWAPPING THE MODEL (important in production)
--------------------------------------------
Everything else here calls embed_text() / embed_texts() below. To use a hosted model,
replace the body of embed_texts(). Nothing else changes except the number of
dimensions (EMBEDDING_DIMS in .env, and "dims" in the vector index definitions):

  OpenAI (text-embedding-3-small, 1536 dims):
      from openai import OpenAI
      response = OpenAI().embeddings.create(model="text-embedding-3-small", input=texts)
      return [item.embedding for item in response.data]

  Amazon Bedrock (Titan Text Embeddings v2; one text per call):
      import boto3, json
      client = boto3.client("bedrock-runtime")
      def embed_one(text):
          body = json.dumps({"inputText": text})
          reply = client.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=body)
          return json.loads(reply["body"].read())["embedding"]
      return [embed_one(t) for t in texts]

  Cohere (embed-english-v3.0, 1024 dims):
      import cohere
      response = cohere.Client().embed(
          texts=texts, model="embed-english-v3.0", input_type="search_document"
      )
      return response.embeddings

IMPORTANT: documents and queries must be embedded with the SAME model. Vectors from
different models cannot be compared.
"""

from functools import cache

import numpy as np
from fastembed import TextEmbedding

from config import get_settings


@cache
def _model() -> TextEmbedding:
    """Load the model on first use (about a second), then reuse it."""
    return TextEmbedding(get_settings().embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Turn a list of texts into a list of vectors (each a list of floats)."""
    vectors = np.array(list(_model().embed(texts)))
    # Scale every vector to length 1. Then cosine similarity and dot product give the
    # same answer, and scores are easy to read (1.0 = identical meaning).
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return [[round(float(x), 6) for x in vector] for vector in vectors]


def embed_text(text: str) -> list[float]:
    """Embed a single text, such as a user's search query."""
    return embed_texts([text])[0]
