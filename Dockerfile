# Imagen base ligera de Python
FROM python:3.11-slim

# Evitar buffering y errores locales
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Crear carpeta de la app
WORKDIR /app

# Copiar requirements e instalarlos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el proyecto completo
COPY . .

# Exponer el puerto
EXPOSE 8000

# Comando de arranque con uvicorn (producción)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]