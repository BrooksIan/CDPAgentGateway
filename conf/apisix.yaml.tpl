{{CONSUMERS_BLOCK}}global_rules:
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
    scheme: {{MCP_SPARK_UPSTREAM_SCHEME}}
    pass_host: {{MCP_SPARK_PASS_HOST}}
{{MCP_SPARK_UPSTREAM_HOST_HEADER}}    timeout:
      connect: 6
      send: 60
      read: 60
{{MCP_SPARK_UPSTREAM_TLS}}    nodes:
      "{{MCP_SPARK_UPSTREAM_HOST}}:{{MCP_SPARK_UPSTREAM_PORT}}": 1
  - id: mcp-hive
    type: roundrobin
    scheme: {{MCP_HIVE_UPSTREAM_SCHEME}}
    pass_host: {{MCP_HIVE_PASS_HOST}}
{{MCP_HIVE_UPSTREAM_HOST_HEADER}}    timeout:
      connect: 6
      send: 60
      read: 60
{{MCP_HIVE_UPSTREAM_TLS}}    nodes:
      "{{MCP_HIVE_UPSTREAM_HOST}}:{{MCP_HIVE_UPSTREAM_PORT}}": 1
  - id: mcp-impala
    type: roundrobin
    scheme: {{MCP_IMPALA_UPSTREAM_SCHEME}}
    pass_host: {{MCP_IMPALA_PASS_HOST}}
{{MCP_IMPALA_UPSTREAM_HOST_HEADER}}    timeout:
      connect: 6
      send: 60
      read: 60
{{MCP_IMPALA_UPSTREAM_TLS}}    nodes:
      "{{MCP_IMPALA_UPSTREAM_HOST}}:{{MCP_IMPALA_UPSTREAM_PORT}}": 1

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

  - id: oauth-protected-resource
    uris:
      - /.well-known/oauth-protected-resource
      - /.well-known/oauth-protected-resource/mcp/spark
      - /.well-known/oauth-protected-resource/mcp/hive
      - /.well-known/oauth-protected-resource/mcp/impala
    methods: ["GET"]
    plugins:
      mocking:
        delay: 0
        content_type: "application/json"
        response_status: 200
        response_example: '{{OAUTH_PRM_JSON}}'
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
        resource_metadata: "{{RESOURCE_METADATA_URL}}"
        token_state_url: "{{KNOX_TOKEN_STATE_URL}}"
        token_state_host: "{{UPSTREAM_HOST}}"
        token_state_tls_verify: {{UPSTREAM_TLS_VERIFY}}
      proxy-rewrite:
        regex_uri:
          - "^/cdp/(.*)"
          - "{{KNOX_PROXY_PREFIX}}/$1"
    upstream_id: cdp

  - id: hdfs-webhdfs
    uri: /cdp/webhdfs*
    methods: ["GET", "HEAD", "PUT"]
    plugins:
      knox-jwt:
        public_key_file: /usr/local/apisix/conf/knox-public.pem
        issuer: "{{KNOX_ISSUER}}"
        expected_alg: "{{KNOX_EXPECTED_ALG}}"
        clock_skew: {{KNOX_CLOCK_SKEW}}
        hide_credentials: false
        realm: knox
        resource_metadata: "{{RESOURCE_METADATA_URL}}"
        token_state_url: "{{KNOX_TOKEN_STATE_URL}}"
        token_state_host: "{{UPSTREAM_HOST}}"
        token_state_tls_verify: {{UPSTREAM_TLS_VERIFY}}
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
        resource_metadata: "{{RESOURCE_METADATA_URL}}"
        token_state_url: "{{KNOX_TOKEN_STATE_URL}}"
        token_state_host: "{{UPSTREAM_HOST}}"
        token_state_tls_verify: {{UPSTREAM_TLS_VERIFY}}
{{MCP_KEY_AUTH_BLOCK}}      limit-count:
        count: {{MCP_RATE_COUNT}}
        time_window: {{MCP_RATE_WINDOW}}
        key_type: var
        key: knox_user
        rejected_code: 429
        rejected_msg: mcp rate limit
        policy: local
        show_limit_quota_header: true
        group: mcp-spark
      proxy-rewrite:
        regex_uri:
          - "^/mcp/spark(.*)"
          - "/mcp$1"
    upstream_id: mcp-spark

  - id: mcp-hive-http
    uri: /mcp/hive*
    methods: ["GET", "HEAD", "POST", "DELETE"]
    plugins:
      knox-jwt:
        public_key_file: /usr/local/apisix/conf/knox-public.pem
        issuer: "{{KNOX_ISSUER}}"
        expected_alg: "{{KNOX_EXPECTED_ALG}}"
        clock_skew: {{KNOX_CLOCK_SKEW}}
        hide_credentials: false
        realm: knox
        resource_metadata: "{{RESOURCE_METADATA_URL}}"
        token_state_url: "{{KNOX_TOKEN_STATE_URL}}"
        token_state_host: "{{UPSTREAM_HOST}}"
        token_state_tls_verify: {{UPSTREAM_TLS_VERIFY}}
{{MCP_KEY_AUTH_BLOCK}}      limit-count:
        count: {{MCP_RATE_COUNT}}
        time_window: {{MCP_RATE_WINDOW}}
        key_type: var
        key: knox_user
        rejected_code: 429
        rejected_msg: mcp rate limit
        policy: local
        show_limit_quota_header: true
        group: mcp-hive
      proxy-rewrite:
        regex_uri:
          - "^/mcp/hive(.*)"
          - "/mcp$1"
    upstream_id: mcp-hive

  - id: mcp-impala-http
    uri: /mcp/impala*
    methods: ["GET", "HEAD", "POST", "DELETE"]
    plugins:
      knox-jwt:
        public_key_file: /usr/local/apisix/conf/knox-public.pem
        issuer: "{{KNOX_ISSUER}}"
        expected_alg: "{{KNOX_EXPECTED_ALG}}"
        clock_skew: {{KNOX_CLOCK_SKEW}}
        hide_credentials: false
        realm: knox
        resource_metadata: "{{RESOURCE_METADATA_URL}}"
        token_state_url: "{{KNOX_TOKEN_STATE_URL}}"
        token_state_host: "{{UPSTREAM_HOST}}"
        token_state_tls_verify: {{UPSTREAM_TLS_VERIFY}}
{{MCP_KEY_AUTH_BLOCK}}      limit-count:
        count: {{MCP_RATE_COUNT}}
        time_window: {{MCP_RATE_WINDOW}}
        key_type: var
        key: knox_user
        rejected_code: 429
        rejected_msg: mcp rate limit
        policy: local
        show_limit_quota_header: true
        group: mcp-impala
      proxy-rewrite:
        regex_uri:
          - "^/mcp/impala(.*)"
          - "/mcp$1"
    upstream_id: mcp-impala

#END
