FROM python:3-alpine
RUN pip install boto3
COPY . /app
WORKDIR /app
CMD python3 -u sshing.py
