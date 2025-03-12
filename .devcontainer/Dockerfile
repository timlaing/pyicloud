FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN apt update \
    && apt install -y openjdk-17-jre-headless \
    && pip install pre-commit \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
