FROM node:22-slim AS build
WORKDIR /app
COPY vendor/OpenTerminal/package.json ./
COPY vendor/OpenTerminal/web/package.json web/
RUN npm install --prefix web
COPY vendor/OpenTerminal/web/ web/
COPY docker/openterminal-middleware.ts web/middleware.ts
RUN npm run build --prefix web

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/web/.next/standalone ./
COPY --from=build /app/web/.next/static ./web/.next/static
EXPOSE 3000
CMD ["node", "server.js"]
