local core = require("apisix.core")
local jwt = require("resty.jwt")
local ngx = ngx
local io = io
local sub = string.sub
local plugin_name = "knox-jwt"

local schema = {
    type = "object",
    properties = {
        public_key_file = {type = "string", minLength = 1},
        issuer = {type = "string", default = "KNOXSSO"},
        expected_alg = {type = "string", default = "RS256"},
        header = {type = "string", default = "authorization"},
        clock_skew = {type = "integer", minimum = 0, default = 60},
        realm = {type = "string", default = "knox"},
        hide_credentials = {type = "boolean", default = false},
    },
    required = {"public_key_file"},
}

local _M = {
    version = 0.1,
    priority = 2598,
    name = plugin_name,
    schema = schema,
}

local key_cache = {}

local function unauthorized(conf, reason)
    core.response.set_header("WWW-Authenticate", "Bearer realm=\"" .. conf.realm .. "\"")
    core.response.set_header("X-Agent-Gateway-Reason", reason)
    return 401, { error = "unauthorized", reason = reason }
end

local function read_file(path)
    local fh, err = io.open(path, "r")
    if not fh then
        return nil, err
    end
    local data = fh:read("*a")
    fh:close()
    if not data or data == "" then
        return nil, "empty public key file"
    end
    return data
end

local function load_public_key(path)
    local cached = key_cache[path]
    if cached then
        return cached
    end
    local pem, err = read_file(path)
    if not pem then
        return nil, err
    end
    key_cache[path] = pem
    return pem
end

local function bearer_token(conf, ctx)
    local header = core.request.header(ctx, conf.header)
    if not header or header == "" then
        return nil, "missing_token"
    end
    local prefix = sub(header, 1, 7)
    if prefix == "Bearer " or prefix == "bearer " then
        local token = sub(header, 8)
        if token == "" then
            return nil, "missing_token"
        end
        if conf.hide_credentials then
            core.request.set_header(ctx, conf.header, nil)
        end
        return token
    end
    return nil, "missing_token"
end

function _M.check_schema(conf)
    return core.schema.check(schema, conf)
end

function _M.rewrite(conf, ctx)
    local token, reason = bearer_token(conf, ctx)
    if not token then
        return unauthorized(conf, reason)
    end

    local public_key, err = load_public_key(conf.public_key_file)
    if not public_key then
        core.log.error("knox-jwt failed to load public key: ", err)
        return 500, { error = "gateway_misconfigured" }
    end

    local jwt_obj = jwt:load_jwt(token)
    if not jwt_obj or not jwt_obj.valid then
        return unauthorized(conf, "invalid_token")
    end

    local alg = jwt_obj.header and jwt_obj.header.alg
    if alg ~= conf.expected_alg then
        return unauthorized(conf, "invalid_alg")
    end

    local verified = jwt:verify_jwt_obj(public_key, jwt_obj)
    if not verified or not verified.verified then
        return unauthorized(conf, "invalid_signature")
    end

    local payload = verified.payload or jwt_obj.payload or {}
    if conf.issuer and payload.iss ~= conf.issuer then
        return unauthorized(conf, "invalid_issuer")
    end
    if not payload.sub or payload.sub == "" then
        return unauthorized(conf, "invalid_subject")
    end

    local now = ngx.time()
    local skew = conf.clock_skew or 60
    if payload.exp and (payload.exp + skew) < now then
        return unauthorized(conf, "expired")
    end
    if payload.nbf and payload.nbf > (now + skew) then
        return unauthorized(conf, "not_yet_valid")
    end

    ctx.knox_user = payload.sub
    ctx.knox_token_id = payload["knox.id"]
    core.request.set_header(ctx, "X-Knox-User", payload.sub)
    if payload["knox.id"] then
        core.request.set_header(ctx, "X-Knox-Token-Id", payload["knox.id"])
    end
end

return _M
