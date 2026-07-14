FROM python:3.14

WORKDIR /app

COPY requirements.txt .

RUN pip3 install --require-hashes -r requirements.txt

COPY . .

CMD ["bash", "run.sh"]