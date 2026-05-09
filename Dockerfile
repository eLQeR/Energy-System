FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY monitoring/requirements.txt      monitoring/requirements.txt
COPY ontology/requirements.txt        ontology/requirements.txt
COPY analyzer/requirements.txt        analyzer/requirements.txt
COPY alerts_server/requirements.txt   alerts_server/requirements.txt
RUN pip install \
      -r monitoring/requirements.txt \
      -r ontology/requirements.txt \
      -r analyzer/requirements.txt \
      -r alerts_server/requirements.txt

COPY shared/        shared/
COPY monitoring/    monitoring/
COPY ontology/      ontology/
COPY analyzer/      analyzer/
COPY alerts_server/ alerts_server/

# Згенерувати тренувальні дані і навчити IsolationForest під час збірки —
# модель опиняється всередині образа, analyzer стартує без init-кроку.
RUN python3 analyzer/generate_synthetic.py \
 && python3 analyzer/train_model.py
