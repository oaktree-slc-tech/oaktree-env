''' Sonador authentication backend for AirFlow. Accepts either a Sonador API token
    or API access ID/secret for credentials, which are then forwarded to Sonador
    for validation.

    Sonador authentication parameters are provided via environment variables.

    * SONADOR_URL: Full URL to the Sonador instance.
    * Authentication credentials (only one of the following is needed)
        - Access ID/Secret
            + SONADOR_ACCESS_ID: Access ID to be used for accessing the Sonador API.
            + SONADOR_SECRET_KEY: Secret key to be used for accessing the Sonador API.
        - API Token
            + SONADOR_APITOKEN: API token to be used for accessing the Sonador API.
                If present, the Sonador API token will taken precedence.
'''
import os, logging
from functools import wraps
from typing import Any, Callable, Optional, Tuple, TypeVar, Union, cast

from flask import Response, current_app, request
from flask_appbuilder.security.sqla.models import User
from flask_login import login_user

from client.utils.urls import validate_url
from client.errors import ClientOperationError

from sonador.apisettings import SONADOR_ACCESS_ID as SONADORENV_ACCESS_ID, \
    SONADOR_SECRET_KEY as SONADORENV_SECRET_KEY, \
    SONADOR_URL as SONADORENV_URL, SONADOR_APITOKEN as SONADORENV_APITOKEN, \
    SONADOR_SERVICE_CLIENT_ID as SONADORENV_SERVICE_CLIENT_ID
from sonador.helpers import API_ACCESS_TOKEN, OAUTH_TOKEN_TYPE_BEARER, initenv_sonador_server

logger = logging.getLogger(__name__)


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

# Sonador connection client: Connection parameters should be passed to the
# application as environment variables. Refer to docstring above for details.
SONADOR_CONN = initenv_sonador_server()
SONADOR_DATA_SERVICE = SONADOR_CONN.get_dataservice(os.environ.get(SONADORENV_SERVICE_CLIENT_ID))


CLIENT_AUTH: Optional[Union[Tuple[str, str], Any]] = None


def init_app(*args, **kwargs):
    ''' Initializes the authentication backend
    '''
    logger.info('Sonador API authentication enabled. Sonador instance: %s' % os.environ.get(SONADORENV_URL))
    logger.info('Sonador Data Service ID: %s' % os.environ.get(SONADORENV_SERVICE_CLIENT_ID))


T = TypeVar('T', bound=Callable)


def auth_current_user() -> Optional[User]:
    ''' Check authentication credentials for the provided
    '''
    auth = request.authorization

    # Sonador credentials are passed using the username/password fields.
    # The username will contain the authentication method and the password
    # will contain the authorization token.
    if auth is None or not auth.username or not auth.password:
        return None

    # Check authentication credentials with Sonador
    ab_security_manager = current_app.appbuilder.sm
    try: cred_auth = SONADOR_DATA_SERVICE.verify_api_credentials(auth.username, auth.password)
    except ClientOperationError as err: cred_auth = None

    # Credentials valid: retrieve or create user instance and authenticate user to Airflow
    if cred_auth and cred_auth.get('granted'):
        
        # Retrieve user by email or username. Users must first be registered in the system
        # to authenticate using Sonador credentials.
        user = ab_security_manager.find_user(email=cred_auth.get('user', {}).get('email'))
        if user is None:
            user = ab_security_manager.find_user(username=cred_auth.get('user', {}).get('username'))
    
    # Invalid credentials
    else: user = None

    # Authenticate user to the API
    if user is not None:
        login_user(user, remember=False)

    return user


def requires_authentication(function: T):
    ''' Decorator for functions that require authentication
    '''
    @wraps(function)
    def decorated(*args, **kwargs):
        if auth_current_user() is not None:
            return function(*args, **kwargs)
        else:
            return Response("Unauthorized", 401, {"WWW-Authenticate": "Sonador API Token, Access ID/Secret"})

    return cast(T, decorated)
