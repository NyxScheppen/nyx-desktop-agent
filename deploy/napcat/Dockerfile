FROM mlikiowa/napcat-docker@sha256:696e9574a0f6fb2f86cbecba8a32048f2a5823117dc9b13c6c02637ad890fa8b

COPY entrypoint.sh /app/nyx-entrypoint.sh
RUN chmod +x /app/nyx-entrypoint.sh

ENTRYPOINT ["/app/nyx-entrypoint.sh"]
