# --- Stage 1: build Tailwind CSS ---
FROM node:20-alpine AS css-builder
WORKDIR /build
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/tailwind.css ./frontend/tailwind.css
COPY frontend/index.html frontend/app.js ./frontend/
RUN npx @tailwindcss/cli -i ./frontend/tailwind.css -o ./frontend/styles-tailwind.css --minify

# --- Stage 2: Python runtime ---
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY --from=css-builder /build/frontend/styles-tailwind.css ./frontend/styles-tailwind.css
RUN mkdir -p data
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
