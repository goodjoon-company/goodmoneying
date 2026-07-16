FROM node:24-slim AS build

ENV TZ=UTC
RUN apt-get update \
  && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci
COPY apps/web ./apps/web
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.29-alpine
ENV TZ=UTC
ENV GOODMONEYING_API_INTERNAL_URL=http://api:8000
ENV GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL=http://upbit-gateway:8001
ENV GOODMONEYING_OPERATOR_TOKEN=local-dev-token
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf.template /etc/nginx/templates/default.conf.template
RUN apk add --no-cache tzdata \
  && command -v envsubst >/dev/null
EXPOSE 80
