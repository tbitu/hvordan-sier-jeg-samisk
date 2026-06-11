FROM rocm/pytorch:rocm7.1.1_ubuntu22.04_py3.11_pytorch_release_2.10.0

WORKDIR /workspace
COPY apps/api /workspace/apps/api
RUN python -m pip install --no-cache-dir --upgrade pip && \
	python -m pip install --no-cache-dir -e /workspace/apps/api[inference]
CMD ["python", "-c", "print('ROCm inference image ready for provider wiring')"]
