FROM hyperledger/fabric-nodeenv:2.4

ARG CC_SERVER_PORT=9999
WORKDIR /usr/src/app

COPY package.json package-lock.json .npmrc ./
RUN npm ci --omit=dev && npm cache clean --force

COPY index.js ./
COPY lib ./lib

ENV PORT=${CC_SERVER_PORT}
EXPOSE ${CC_SERVER_PORT}

CMD ["sh", "-c", "npm run start:server-nontls"]
