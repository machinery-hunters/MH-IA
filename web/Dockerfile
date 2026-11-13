# Imagen base fijada por digest (multi-arquitectura). Dependabot propone las subidas.
FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS build
WORKDIR /app
# Los rewrites de next.config.ts se resuelven en BUILD: la URL interna de la
# API entra como build arg (por defecto, el nombre del servicio en compose).
ARG API_INTERNAL_URL=http://mhia-api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Usuario sin privilegios: el contenedor nunca corre como root. En ejecución solo
# hace falta node: se quitan npm, npx, corepack y yarn, que traen dependencias con
# avisos (Trivy en CI) y no se usan para servir la GUI.
RUN addgroup -S -g 10001 mhia && adduser -S -u 10001 -G mhia mhia \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
              /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
              /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-*

COPY --from=build --chown=mhia:mhia /app/.next/standalone ./
COPY --from=build --chown=mhia:mhia /app/.next/static ./.next/static
COPY --from=build --chown=mhia:mhia /app/public ./public

USER mhia
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
