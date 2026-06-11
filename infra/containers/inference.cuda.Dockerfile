FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /workspace
COPY apps/api /workspace/apps/api
RUN python -m pip install --no-cache-dir -e /workspace/apps/api[inference]
CMD ["python", "-c", "print('CUDA inference image ready for provider wiring')"]
