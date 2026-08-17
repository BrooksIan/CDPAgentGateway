global_rules:
  - id: request-id
    plugins:
      request-id:
        header_name: X-Request-Id
        include_in_response: true

upstreams:
  - id: cdp
    type: roundrobin
    scheme: {{UPSTREAM_SCHEME}}
    pass_host: node
    timeout:
      connect: 10
      send: 120
      read: 120
    nodes:
      "{{UPSTREAM_HOST}}:{{UPSTREAM_PORT}}": 1
{{TLS_BLOCK}}
  - id: mcp-spark
    type: roundrobin
    scheme: http
    timeout:
      connect: 6
      send: 60
      read: 60
    nodes:
      "mcp-spark:8080": 1

routes:
  - id: health
    uri: /health
    methods: ["GET"]
    plugins:
      mocking:
        delay: 0
        content_type: "application/json"
        response_status: 200
        response_example: '{"status":"ok","service":"agent-gateway"}'
    upstream_id: cdp

  - id: spark-livy
    uri: /cdp/livy_for_spark3*
    methods: ["GET", "HEAD"]
    plugins:
      knox-jwt:
        public_key_file: /usr/local/apisix/conf/knox-public.pem
        issuer: "{{KNOX_ISSUER}}"
        expected_alg: "{{KNOX_EXPECTED_ALG}}"
        clock_skew: {{KNOX_CLOCK_SKEW}}
        hide_credentials: false
        realm: knox
      proxy-rewrite:
        regex_uri:
          - "^/cdp/(.*)"
          - "{{KNOX_PROXY_PREFIX}}/$1"
    upstream_id: cdp

  - id: mcp-spark-http
    uri: /mcp/spark*
    methods: ["GET", "HEAD", "POST", "DELETE"]
    plugins:
      knox-jwt:
        public_key_file: /usr/local/apisix/conf/knox-public.pem
        issuer: "{{KNOX_ISSUER}}"
        expected_alg: "{{KNOX_EXPECTED_ALG}}"
        clock_skew: {{KNOX_CLOCK_SKEW}}
        hide_credentials: false
        realm: knox
      proxy-rewrite:
        regex_uri:
          - "^/mcp/spark(.*)"
          - "/mcp$1"
    upstream_id: mcp-spark

#END
