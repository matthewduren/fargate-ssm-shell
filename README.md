# Fargate SSM shell

Provides shell access into a fargate container.

## Prerequisites
* You must have the SSM agent installed
* ssm-shell.sh must exist at /opt/ssm-shell.sh
* Sample debian-based Dockerfile for your app:
```docker
FROM debian:stretch

RUN apt-get update && apt-get install curl -y

RUN curl https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_amd64/amazon-ssm-agent.deb -o amazon-ssm-agent.deb

RUN dpkg -i amazon-ssm-agent.deb

COPY ssm-shell.sh /opt/ssm-shell.sh

CMD your/normal/application/entrypoint
```
