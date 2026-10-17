FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# Los rewrites de next.config.ts se resuelven en BUILD: la URL interna de la
# API entra como build arg (por defecto, el nombre del servicio en compose).
ARG API_INTERNAL_URL=http://mhia-api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Usuario sin privilegios: el contenedor nunca corre como root.
RUN addgroup -S -g 10001 mhia && adduser -S -u 10001 -G mhia mhia

COPY --from=build --chown=mhia:mhia /app/.next/standalone ./
COPY --from=build --chown=mhia:mhia /app/.next/static ./.next/static
COPY --from=build --chown=mhia:mhia /app/public ./public

USER mhia
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
