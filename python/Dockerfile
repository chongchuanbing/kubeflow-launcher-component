FROM ubuntu:16.04

MAINTAINER chongchuanbing@126.com

RUN apt-get update -y && \
    apt-get install --no-install-recommends -y -q ca-certificates python-dev python-setuptools wget && \
    easy_install pip && \
    pip install pyyaml==3.12 kubernetes==10.0.1 enum34

ADD src /ml

WORKDIR /ml

ENTRYPOINT ["python", "launcher.py"]