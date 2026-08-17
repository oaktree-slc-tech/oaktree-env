''' Sonador authentication app for MLflow. Provides Single Sign On via OpenID Connect
    (mediated by a Sonador Data Service) for browser access to the MLflow UI, and
    validates Sonador API tokens/bearer tokens for programmatic access to the MLflow
    REST API. Credentials are forwarded to Sonador for validation via the data service
    introspection endpoint.

    The app is registered under the `mlflow.app` entry point (as `sonador-auth`) and is
    enabled by launching the tracking server with:

        mlflow server --app-name sonador-auth ...

    Sonador authentication parameters are provided via environment variables.

    * SONADOR_URL: Full URL to the Sonador instance.
    * SONADOR_SERVICE_CLIENT_ID: Client ID of the data service used for authentication.
    * Authentication credentials (only one of the following is needed)
        - Access ID/Secret
            + SONADOR_ACCESS_ID: Access ID to be used for accessing the Sonador API.
            + SONADOR_SECRET_KEY: Secret key to be used for accessing the Sonador API.
        - API Token
            + SONADOR_APITOKEN: API token to be used for accessing the Sonador API.
                If present, the Sonador API token will taken precedence.

    Optional variables:

    * SONADOR_SERVICE_OPENID_SCOPE: OpenID scope requested during login
        (default: "openid email profile").
    * SONADOR_PUBLIC_URL: Browser-facing URL of the Sonador instance. Used when
        constructing the login redirect if the internal SONADOR_URL is not reachable
        from the user's browser. Defaults to SONADOR_URL.
    * SONADOR_SERVICE_REDIRECT_URL: Explicit OpenID callback URL for the deployment.
        Defaults to "{scheme}://{host}/oauth-authorized/sonador" derived from the
        active request. The value (derived or explicit) must be registered in the
        data service "Callback URL" list.
    * SONADOR_TOKEN_CACHE_TTL: Seconds introspection responses are cached before
        credentials are re-validated with Sonador (default: 60).
    * MLFLOW_FLASK_SERVER_SECRET_KEY: Secret key used to sign the Flask session
        cookie. Should be set to a value unique for the deployment.

    Programmatic access (choose one):

    * MLFLOW_TRACKING_TOKEN="<sonador-api-token>" (sent as "Authorization: Bearer")
    * MLFLOW_TRACKING_USERNAME="api-token" / MLFLOW_TRACKING_PASSWORD="<sonador-api-token>"
        (mirrors the Sonador Airflow API auth backend, which accepts the token type
        as the username and the token as the password)
'''
import os, time, logging, secrets, threading, base64
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from flask import request as flask_request, session as flask_session, \
    redirect as flask_redirect, Response as FlaskResponse, g as flask_g

from mlflow.environment_variables import _MLFLOW_SGI_NAME

from client.utils.urls import validate_url
from client.errors import ConfigurationError

from sonador.apisettings import SONADOR_ACCESS_ID as SONADORENV_ACCESS_ID, \
    SONADOR_SECRET_KEY as SONADORENV_SECRET_KEY, \
    SONADOR_URL as SONADORENV_URL, SONADOR_APITOKEN as SONADORENV_APITOKEN, \
    SONADOR_SERVICE_CLIENT_ID as SONADORENV_SERVICE_CLIENT_ID
from sonador.helpers import API_ACCESS_TOKEN, OAUTH_TOKEN_TYPE_BEARER, initenv_sonador_server

logger = logging.getLogger(__name__)


# SSO / OpenID Connect Constants
SONADOR_SERVICE_OPENID_SCOPE = os.environ.get('SONADOR_SERVICE_OPENID_SCOPE', 'openid email profile')
SONADOR_TOKEN_CACHE_TTL = int(os.environ.get('SONADOR_TOKEN_CACHE_TTL', '60'))
SONADOR_AUTHTOKEN_TYPES = (OAUTH_TOKEN_TYPE_BEARER, API_ACCESS_TOKEN)

# Session keys. Token keys match the Sonador Airflow integration; the OIDC
# state/nonce keys match the Sonador FastAPI integration (sonador_fastapi.oauth).
SESSION_AUTHTOKEN = 'sonador-authtoken'
SESSION_AUTHTOKEN_TYPE = 'sonador-authtoken-type'
SESSION_OIDC_STATE = 'oidc-state'
SESSION_OIDC_NONCE = 'oidc-nonce'
SESSION_LOGIN_NEXT = 'sonador-login-next'

# Application routes
ROUTE_OAUTH_CALLBACK = '/oauth-authorized/sonador'
ROUTE_LOGIN = '/sonador/login'
ROUTE_LOGOUT = '/sonador/logout'

# Paths which do not require authentication. Health/version endpoints are used
# by orchestration liveness checks and do not expose tracking data.
UNPROTECTED_ROUTES = ('/health', '/version', '/favicon.ico',
    ROUTE_OAUTH_CALLBACK, ROUTE_LOGIN, ROUTE_LOGOUT)


# Sonador connection client: Connection parameters should be passed to the
# application as environment variables. Refer to docstring above for details.
# Initialization is deferred to create_app so the module can be imported
# (e.g. for entry point resolution) without a configured environment.
SONADOR_CONN = None
SONADOR_DATA_SERVICE = None


def init_sonador():
    ''' Validate the environment and initialize the Sonador server connection and
        data service instance used for authentication.
    '''
    global SONADOR_CONN, SONADOR_DATA_SERVICE
    if SONADOR_CONN is not None and SONADOR_DATA_SERVICE is not None:
        return SONADOR_CONN, SONADOR_DATA_SERVICE

    # Check for Sonador connection URL
    if not os.environ.get(SONADORENV_URL):
        raise ValueError('Unable to initialize Sonador server, invalid URL: %s' % os.environ.get(SONADORENV_URL))
    validate_url(os.environ.get(SONADORENV_URL))

    # Check for Sonador credentials
    if not os.environ.get(SONADORENV_APITOKEN) \
        and (not os.environ.get(SONADORENV_ACCESS_ID) or not os.environ.get(SONADORENV_SECRET_KEY)):
        raise ValueError('Unable to initialize Sonador server, missing access credentials. Check API token or access ID/secret.')

    # Check for Sonador Service ID
    if not os.environ.get(SONADORENV_SERVICE_CLIENT_ID):
        raise ValueError('Unable to initialize data service, invalid ID: %s'
            % os.environ.get(SONADORENV_SERVICE_CLIENT_ID))

    SONADOR_CONN = initenv_sonador_server()
    SONADOR_DATA_SERVICE = SONADOR_CONN.get_dataservice(os.environ.get(SONADORENV_SERVICE_CLIENT_ID))
    if not SONADOR_DATA_SERVICE.openid_allow_auth:
        raise ConfigurationError(('Unable to enable SSO, Sonador Data Service (uid="%s") does not have OpenID Connect '
            'authentication enabled.') % (SONADOR_DATA_SERVICE.pk))

    return SONADOR_CONN, SONADOR_DATA_SERVICE


# Token introspection cache: introspection responses are cached (per worker process)
# to avoid a round-trip to Sonador on every request. Entries expire after
# SONADOR_TOKEN_CACHE_TTL seconds, after which credentials are re-validated.
_TOKEN_CACHE = {}
_TOKEN_CACHE_LOCK = threading.Lock()


def sonador_authtoken2userinfo(authtoken_type, authtoken):
    ''' Introspect the provided token and retrieve the associated user info via the
        data service. Successful introspections are cached for a short interval.

        @returns dict or None if the credentials could not be validated
    '''
    cache_key = (authtoken_type, authtoken)
    now = time.monotonic()

    # Attempt to retrieve user info from cache
    with _TOKEN_CACHE_LOCK:
        _cached = _TOKEN_CACHE.get(cache_key)
        if _cached and _cached[0] > now:
            return _cached[1]

    # Retrieve user info from Sonador via token introspection
    try:
        user_info = SONADOR_DATA_SERVICE.verify_api_credentials(authtoken_type, authtoken)
    except Exception as err:
        logger.warning('Unable to validate Sonador credentials (token_type=%s): %s' % (authtoken_type, err))
        return None

    # Ensure the introspection response describes an active user
    if not user_info or not user_info.get('user') or user_info.get('active') is False:
        logger.warning('Sonador token introspection returned an inactive/invalid token (token_type=%s)'
            % authtoken_type)
        return None

    # Cache and prune expired entries
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE[cache_key] = (now + SONADOR_TOKEN_CACHE_TTL, user_info)
        for _key in [k for k, v in _TOKEN_CACHE.items() if v[0] <= now]:
            del _TOKEN_CACHE[_key]

    return user_info


def create_oidc_state(length=32, lowercase=True):
    ''' Generate a state string which can be used to prevent hijacking of the application OIDC
        auth workflow. (Mirrors the Sonador FastAPI integration, sonador_fastapi.oauth.)

        @input length (int, default=32): length of the token to generate
        @input lowercase (bool, default=True): normalize output to lowercase. This is done
            to prevent errors when inspecting values that might have been normalized
            by external systems.
    '''
    _state = secrets.token_urlsafe(length)
    return _state.lower() if lowercase else _state


def create_oidc_nonce(length=32, lowercase=True):
    ''' Generate a nonce to prevent hijacking of the application OIDC auth workflow.
        (Mirrors the Sonador FastAPI integration, sonador_fastapi.oauth.)

        @input length (int, default=32): length of the token to generate
        @input lowercase (bool, default=True): normalize output to lowercase. This is done
            to prevent errors when inspecting values that might have been normalized
            by external systems.
    '''
    _nonce = secrets.token_urlsafe(length)
    return _nonce.lower() if lowercase else _nonce


def merge_url_query(url, params):
    ''' Merge the provided parameters into the query string of the URL. Existing
        query parameters (e.g. signed URL components) are preserved.
    '''
    _parts = urlsplit(url)
    _query = parse_qsl(_parts.query, keep_blank_values=True)
    _query.extend(params.items())

    return urlunsplit((_parts.scheme, _parts.netloc, _parts.path, urlencode(_query), _parts.fragment))


def sonador_authorize_url(state):
    ''' Construct the browser-facing OpenID Connect authorization URL for the data
        service. If SONADOR_PUBLIC_URL is provided, the scheme/host of the redirect
        is rewritten so the URL is reachable from the user's browser.
    '''
    _authorize = SONADOR_CONN.apiurl(SONADOR_DATA_SERVICE.url_oidc_authorize)

    # Rewrite internal scheme/netloc with the browser-facing components (if configured)
    _public = os.environ.get('SONADOR_PUBLIC_URL')
    if _public:
        _pparts, _aparts = urlsplit(_public), urlsplit(_authorize)
        _authorize = urlunsplit((_pparts.scheme, _pparts.netloc, _aparts.path, _aparts.query, _aparts.fragment))

    return merge_url_query(_authorize, {
        'client_id': SONADOR_DATA_SERVICE.openid_client_id,
        'redirect_uri': sonador_redirect_url(),
        'response_type': 'code',
        'scope': SONADOR_SERVICE_OPENID_SCOPE,
        'state': state,
        'nonce': flask_session.get(SESSION_OIDC_NONCE) or create_oidc_nonce(),
    })


def sonador_redirect_url():
    ''' Retrieve the OpenID callback URL for the deployment. Uses
        SONADOR_SERVICE_REDIRECT_URL when provided, otherwise the URL is derived
        from the host of the active request.
    '''
    return os.environ.get('SONADOR_SERVICE_REDIRECT_URL') \
        or flask_request.host_url.rstrip('/') + ROUTE_OAUTH_CALLBACK


def request_credentials(request):
    ''' Extract Sonador credentials from the active request (if present).

        Supported locations (checked in order):
        1. "Authorization: Bearer <token>" header (MLFLOW_TRACKING_TOKEN)
        2. "Api-Token: <token>" header (Sonador request header convention)
        3. HTTP Basic auth where the username is a Sonador token type
           ("Bearer" or "api-token") and the password is the token value
           (MLFLOW_TRACKING_USERNAME/MLFLOW_TRACKING_PASSWORD, mirroring the
           Airflow `auth_user_db` integration)

        @returns (token_type, token) tuple or None
    '''
    _header = request.headers.get('Authorization', '')
    if _header.lower().startswith('bearer '):
        return (OAUTH_TOKEN_TYPE_BEARER, _header[len('bearer '):].strip())

    _apitoken = request.headers.get(API_ACCESS_TOKEN)
    if _apitoken:
        return (API_ACCESS_TOKEN, _apitoken.strip())

    _basic = request.authorization
    if _basic and _basic.type == 'basic' and _basic.username in SONADOR_AUTHTOKEN_TYPES:
        return (_basic.username, _basic.password)

    return None


def login_redirect_response():
    ''' Initiate the OpenID Connect authorization-code workflow by redirecting the
        browser to the Sonador data service authorization endpoint.

        State/nonce values are generated lowercase (via create_oidc_state and
        create_oidc_nonce, mirroring the Sonador FastAPI integration): querystring
        components may be lower-cased by external systems during the data service
        redirect encoding, so tokens must contain no significant case to survive
        the round trip intact.
    '''
    flask_session[SESSION_OIDC_STATE] = _state = create_oidc_state()
    flask_session[SESSION_OIDC_NONCE] = create_oidc_nonce()

    # Preserve the originally requested resource (path/query only, same-origin)
    if flask_request.method in ('GET', 'HEAD') and flask_request.path not in (ROUTE_LOGIN,):
        flask_session[SESSION_LOGIN_NEXT] = flask_request.full_path.rstrip('?')

    return flask_redirect(sonador_authorize_url(_state))


def unauthorized_response():
    ''' 401 response for API requests without valid credentials
    '''
    return FlaskResponse('{"error_code": "UNAUTHENTICATED", "message": "Valid Sonador credentials are required. '
            'Provide a Sonador API token via \'Authorization: Bearer\' or sign in via the browser."}',
        status=401, mimetype='application/json',
        headers={ 'WWW-Authenticate': 'Bearer realm="sonador"' })


def sonador_oauth_authorized():
    ''' Process login redirect from Sonador as part of authorization_code workflow

        1. Verify the state parameter matches the value generated at login.
        2. Exchange code for auth token.
        3. Validate the token/user via data service introspection.
        4. Stash token details in the session so subsequent requests are authenticated.
        5. Redirect to the originally requested resource (or the index).
    '''
    auth_code = flask_request.args.get('code')
    state = flask_request.args.get('state')

    if not auth_code:
        raise ValueError('Invalid OpenID request structure, Unable to retrieve authorization code')

    # Check state (set at the beginning of the workflow) to ensure that there
    # hasn't been any interference by middlemen. State values are generated
    # lowercase (see create_oidc_state) so the comparison survives querystring
    # normalization by external systems. On mismatch, an explicit error page is
    # returned (never an automatic restart, which can produce a redirect loop
    # and replay single-use authorization codes).
    _auth_state = flask_session.pop(SESSION_OIDC_STATE, None)
    if not _auth_state or (_auth_state != state):
        logger.error('Invalid OpenID connect state. Session: "%s". Query: "%s"' % (_auth_state, state))
        return FlaskResponse('Invalid OpenID connect state. '
                '<a href="%s">Retry login</a>.' % ROUTE_LOGIN,
            status=400)

    # Exchange code for authorization token
    _token = SONADOR_DATA_SERVICE.oidc_fetch_authtoken(auth_code, rdata={
        'scope': SONADOR_SERVICE_OPENID_SCOPE,
    })

    _sonador_authtoken = _token.get('token')
    _sonador_authtoken_type = _token.get('token_type')

    # Validate token and retrieve user info via introspection
    _user_info = sonador_authtoken2userinfo(_sonador_authtoken_type, _sonador_authtoken)
    if not _user_info:
        return FlaskResponse('Unable to validate Sonador credentials. <a href="%s">Retry login</a>.' % ROUTE_LOGIN,
            status=403)

    # Stash token so that it is available for subsequent requests, clean up OIDC session
    flask_session[SESSION_AUTHTOKEN] = _sonador_authtoken
    flask_session[SESSION_AUTHTOKEN_TYPE] = _sonador_authtoken_type
    flask_session.pop(SESSION_OIDC_NONCE, None)

    logger.info('Sonador SSO login: user=%s' % _user_info.get('user', {}).get('username'))
    return flask_redirect(flask_session.pop(SESSION_LOGIN_NEXT, None) or '/')


def sonador_login():
    ''' Start the SSO workflow explicitly. An optional `next` query parameter
        (relative path only) sets the post-login redirect target.
    '''
    _next = flask_request.args.get('next')
    if _next and _next.startswith('/') and not _next.startswith('//'):
        flask_session[SESSION_LOGIN_NEXT] = _next

    return login_redirect_response()


def sonador_logout():
    ''' Clear the active session and redirect to the login endpoint
    '''
    flask_session.pop(SESSION_AUTHTOKEN, None)
    flask_session.pop(SESSION_AUTHTOKEN_TYPE, None)
    return FlaskResponse('Signed out of MLflow. <a href="%s">Sign in with Sonador</a>.' % ROUTE_LOGIN, status=200)


def sonador_authenticate_request():
    ''' `before_request` hook which enforces Sonador authentication for all protected
        routes. Requests are allowed if:

        1. The route is unprotected (health/version/login machinery), OR
        2. The request carries Sonador API credentials which introspect successfully, OR
        3. The session contains a Sonador auth token (from a prior SSO login) which
           (re)introspects successfully.

        Browser requests without credentials are redirected into the SSO workflow;
        API requests receive 401.
    '''
    if flask_request.path in UNPROTECTED_ROUTES:
        return None

    # API credentials provided with the request (token validation)
    _credentials = request_credentials(flask_request)
    if _credentials:
        _user_info = sonador_authtoken2userinfo(*_credentials)
        if _user_info:
            flask_g.sonador_user = _user_info.get('user')
            return None
        return unauthorized_response()

    # Session token from a prior SSO login. Tokens are periodically re-validated
    # against Sonador (cache TTL) so revoked tokens lose access.
    _session_token = flask_session.get(SESSION_AUTHTOKEN)
    _session_token_type = flask_session.get(SESSION_AUTHTOKEN_TYPE)
    if _session_token and _session_token_type:
        _user_info = sonador_authtoken2userinfo(_session_token_type, _session_token)
        if _user_info:

            # Cross-origin protection: session-cookie authenticated requests which
            # modify state must originate from the application itself.
            if flask_request.method not in ('GET', 'HEAD', 'OPTIONS'):
                _origin = flask_request.headers.get('Origin')
                if _origin and urlsplit(_origin).netloc not in ('', flask_request.host):
                    return FlaskResponse('Cross-origin request rejected', status=403)

            flask_g.sonador_user = _user_info.get('user')
            return None

        # Session token is no longer valid, clear and fall through to login
        flask_session.pop(SESSION_AUTHTOKEN, None)
        flask_session.pop(SESSION_AUTHTOKEN_TYPE, None)

    # No valid credentials: redirect browsers into the SSO workflow, 401 for API clients
    if flask_request.method in ('GET', 'HEAD') \
        and flask_request.accept_mimetypes.accept_html:
        return login_redirect_response()

    return unauthorized_response()


def add_sonador_fastapi_middleware(fastapi_app, flask_app):
    ''' Enforce Sonador authentication at the ASGI layer. MLflow (3.x) serves the
        tracking server with uvicorn by default: the Flask application is mounted
        within a FastAPI app which also serves native routes (artifacts, gateway,
        jobs, traces) that bypass Flask's `before_request` hooks. This middleware
        mirrors `sonador_authenticate_request` for those routes (and acts as a
        first authentication gate for all others).
    '''
    from starlette.responses import Response as StarletteResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool
    from flask.sessions import SecureCookieSessionInterface

    # Serializer used to decode/verify the (signed) Flask session cookie so SSO
    # browser sessions are honored on FastAPI-native routes
    _session_serializer = SecureCookieSessionInterface().get_signing_serializer(flask_app)
    _session_cookie_name = flask_app.config.get('SESSION_COOKIE_NAME', 'session')

    @fastapi_app.middleware('http')
    async def sonador_asgi_auth(request, call_next):
        if request.url.path in UNPROTECTED_ROUTES:
            return await call_next(request)

        # Extract Sonador API credentials from request headers (mirrors request_credentials)
        _credentials, _from_session = None, False
        _header = request.headers.get('authorization', '')
        if _header.lower().startswith('bearer '):
            _credentials = (OAUTH_TOKEN_TYPE_BEARER, _header[len('bearer '):].strip())
        elif request.headers.get(API_ACCESS_TOKEN):
            _credentials = (API_ACCESS_TOKEN, request.headers.get(API_ACCESS_TOKEN).strip())
        elif _header.lower().startswith('basic '):
            try:
                _username, _, _password = base64.b64decode(_header[len('basic '):]).decode('utf-8').partition(':')
                if _username in SONADOR_AUTHTOKEN_TYPES:
                    _credentials = (_username, _password)
            except Exception:
                pass

        # Fall back to the Flask session cookie (browser SSO sessions)
        if _credentials is None and _session_serializer is not None:
            _cookie = request.cookies.get(_session_cookie_name)
            if _cookie:
                try:
                    _session_data = _session_serializer.loads(_cookie)
                except Exception:
                    _session_data = None
                if _session_data and _session_data.get(SESSION_AUTHTOKEN):
                    _credentials = (_session_data.get(SESSION_AUTHTOKEN_TYPE),
                        _session_data.get(SESSION_AUTHTOKEN))
                    _from_session = True

        # Validate credentials via (cached) introspection
        if _credentials and _credentials[0] and _credentials[1]:
            _user_info = await run_in_threadpool(sonador_authtoken2userinfo, *_credentials)
            if _user_info:

                # Cross-origin protection for session-cookie authenticated writes
                if _from_session and request.method not in ('GET', 'HEAD', 'OPTIONS'):
                    _origin = request.headers.get('origin')
                    if _origin and urlsplit(_origin).netloc not in ('', request.headers.get('host', '')):
                        return StarletteResponse('Cross-origin request rejected', status_code=403)

                return await call_next(request)

        # No valid credentials: redirect browsers into the SSO workflow (via the
        # Flask login route, which stashes state in the session), 401 for API clients
        if request.method in ('GET', 'HEAD') and 'text/html' in request.headers.get('accept', ''):
            _next = request.url.path + (('?' + request.url.query) if request.url.query else '')
            return RedirectResponse('%s?%s' % (ROUTE_LOGIN, urlencode({ 'next': _next })), status_code=302)

        return StarletteResponse('{"error_code": "UNAUTHENTICATED", "message": "Valid Sonador credentials '
                'are required."}',
            status_code=401, media_type='application/json',
            headers={ 'WWW-Authenticate': 'Bearer realm="sonador"' })


def create_app(app=None):
    ''' Initialize the MLflow Flask application with Sonador Data Service mediated
        authentication. Registered under the `mlflow.app` entry point as `sonador-auth`
        and enabled via `mlflow server --app-name sonador-auth`.
    '''
    if app is None:
        from mlflow.server import app as mlflow_app
        app = mlflow_app

    # Initialize Sonador connection and validate data service configuration
    init_sonador()

    # Session configuration: the session cookie stores the Sonador auth token for
    # browser (SSO) access. The secret key should be unique per deployment.
    _secret = os.environ.get('MLFLOW_FLASK_SERVER_SECRET_KEY')
    if not _secret:
        logger.warning('MLFLOW_FLASK_SERVER_SECRET_KEY is not set, using an ephemeral session secret. '
            'Sessions will not survive server restarts and will not be shared between workers.')
        _secret = secrets.token_urlsafe(32)

    app.secret_key = _secret
    app.config.update(
        SESSION_COOKIE_NAME='mlflow-sonador-session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )

    # Register SSO routes and the authentication hook
    app.add_url_rule(ROUTE_OAUTH_CALLBACK, view_func=sonador_oauth_authorized, methods=['GET'])
    app.add_url_rule(ROUTE_LOGIN, view_func=sonador_login, methods=['GET'])
    app.add_url_rule(ROUTE_LOGOUT, view_func=sonador_logout, methods=['GET'])
    app.before_request(sonador_authenticate_request)

    logger.info('Sonador authentication enabled for MLflow (data service uid="%s")'
        % SONADOR_DATA_SERVICE.pk)

    # MLflow 3.x serves the tracking server with uvicorn by default. When running
    # under uvicorn, wrap the Flask application with the MLflow FastAPI app (which
    # provides native artifact/gateway/job routes) and enforce Sonador
    # authentication at the ASGI layer for the routes served outside of Flask.
    if _MLFLOW_SGI_NAME.get() == 'uvicorn':
        from mlflow.server.fastapi_app import create_fastapi_app
        fastapi_app = create_fastapi_app(app)
        add_sonador_fastapi_middleware(fastapi_app, app)
        return fastapi_app

    return app
