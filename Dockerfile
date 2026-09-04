FROM node:26-alpine AS builder
# set working directory (must NOT be `/`: Tailwind 4 auto-scans the whole
# working directory for class names, and scanning the container root —
# /proc, /sys, /usr, ... — makes `vite build` hang until it is OOM-killed)
WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY index.html tsconfig*.json vite.config.ts ./
COPY public ./public
COPY src ./src

# Prevent esbuild/tsc from oversubscribing CPU/memory beyond the container's
# actual cgroup limits (they otherwise see the host's full core/RAM count,
# which can make the build hang for a long time before being SIGKILLed).
ENV GOMAXPROCS=2
ENV NODE_OPTIONS=--max-old-space-size=2048
ARG VITE_ADMIN_PATH=
ARG VITE_LOCAL_ADMIN_PATH=
ARG VITE_GA_TRACKING_ID=
ENV VITE_ADMIN_PATH=$VITE_ADMIN_PATH
ENV VITE_LOCAL_ADMIN_PATH=$VITE_LOCAL_ADMIN_PATH
ENV VITE_GA_TRACKING_ID=$VITE_GA_TRACKING_ID

RUN npm run build

# production
FROM nginx:stable-alpine
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
