FROM alpine:3.21
RUN apk add --no-cache aria2
COPY aria2.conf /etc/aria2.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 6800 6881 6881/udp
ENTRYPOINT ["/entrypoint.sh"]
