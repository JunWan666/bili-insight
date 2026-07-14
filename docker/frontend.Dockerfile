# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS builder

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /build/dist/ /usr/share/nginx/html/

RUN chown -R nginx:nginx /usr/share/nginx/html

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
    CMD wget --quiet --output-document=- http://127.0.0.1:8080/nginx-health >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
