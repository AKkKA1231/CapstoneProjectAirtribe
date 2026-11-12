FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts ./
RUN npm ci
COPY frontend/index.html ./
COPY frontend/src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
