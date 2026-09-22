FROM node:22-slim AS build
WORKDIR /app
COPY vendor/OpenTerminal/server/package.json server/
RUN npm install --prefix server
COPY vendor/OpenTerminal/server/tsconfig.json server/
COPY vendor/OpenTerminal/server/src server/src
RUN npm run build --prefix server

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY vendor/OpenTerminal/server/package.json ./server/package.json
RUN npm install --prefix server --omit=dev
COPY --from=build /app/server/dist ./server/dist
EXPOSE 4000
CMD ["node", "server/dist/index.js"]
