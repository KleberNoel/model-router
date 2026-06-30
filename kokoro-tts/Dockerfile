FROM python:3.11-slim

RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    kokoro \
    soundfile \
    fastapi \
    uvicorn \
    huggingface_hub

ENV CUDA_VISIBLE_DEVICES=""
ENV HF_HOME=/data/hf

COPY kokoro_tts.py /app/

WORKDIR /app
EXPOSE 8881

CMD ["uvicorn", "kokoro_tts:app", "--host", "0.0.0.0", "--port", "8881"]
