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
from functools import wraps, cached_property
from typing import Any, Callable, Awaitable, Optional, Tuple, TypeVar, Union, cast
from jwt import InvalidTokenError

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from airflow.configuration import conf
from airflow.providers.fab.auth_manager.fab_auth_manager import FabAuthManager
from airflow.providers.fab.auth_manager.security_manager.override import FabAirflowSecurityManagerOverride
from airflow.exceptions import AirflowException

from flask import abort as flash_abort, request as flask_request, make_response as flask_make_response, \
    redirect as flask_redirect
from flask_appbuilder.views import expose
from flask_appbuilder.security.views import AuthOAuthView
from flask_appbuilder.const import AUTH_OAUTH
from flask import session as flask_session
from flask_login import login_user as flask_login_user

from client.utils.urls import validate_url
from client.utils.object import pick, omit
from client.errors import ConfigurationError, ClientOperationError

from sonador import apisettings as sonador_api
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
if not SONADOR_DATA_SERVICE.openid_allow_auth:
    raise ConfigurationError(('Unable to enable SSO, Sonador Data Service (uid="%s") does not have OpenID Connect '
        "authentication enabled.") % (SONADOR_DATA_SERVICE.pk))


# SSO / OpenID Connect Constants
SONADOR_SERVICE_OPENID_SCOPE = os.environ.get('SONADOR_SERVICE_OPENID_SCOPE', 'openid email profile')


def sonador_authtoken2user(sm, authtoken_type, authtoken, dataservice=SONADOR_DATA_SERVICE):
    ''' Introspect the provided token and retrieve the associated user. As part of the introspection,
        user attributes/properties are updated within the Flask database.
    '''
    # Retrieve user info from Sonador via token introspection
    user_info = dataservice.verify_api_credentials(authtoken_type, authtoken)

    # Map Sonador attributes to Airflow User properties
    _user_data = pick(user_info['user'], ('username', 'first_name', 'last_name', 'email'))
    _user_data.update({
        'role': sm.find_role('Admin' if (
                user_info['user'].get('is_staff') or user_info['user'].get('is_superuser')) \
            else 'User'),
    })

    # Retrieve/update Airflow user instance
    _user = sm.find_user(username=_user_data.get('username'))
    if not _user:
        _user = sm.add_user(**_user_data)
    else:
        
        # Update role and user attributes
        _user.roles = [_user_data['role']]
        for _attr,_val in omit(_user_data, ('role',)).items():
            setattr(_user, _attr, _val)

        # Commit changes to the database
        _success = sm.update_user(_user)

    return _user


class SonadorAuthOAuthView(AuthOAuthView):
    ''' Authorizaton view for Data Service Open ID Connect mediated loging for Airflow
    '''
    @expose('/oauth-authorized/sonador')
    def oauth_authorized(self):
        ''' Process login redirect from Sonador as part of authorization_code workflow

            1. Exchange code for auth token.
            2. Stash token details in session so it is available for use from within Airflow machinery.
            3. Create user account (if it does not already exist), set role from current permissions.
            4. Login user to Airflow
            5. Redirect to the index             
        '''
        auth_code = flask_request.args.get('code')
        state = flask_request.args.get('state')
        nonce = flask_request.args.get('nonce')

        logger.warn('oAuth request parameters: auth-code=%s state=%s nonce=%s' % (auth_code, state, nonce))

        if not auth_code:
            raise ValueError('Invalid OpenID request structure, Unable to retrieve authorization code')

        # Exchange code for authorization token
        _token = SONADOR_DATA_SERVICE.oidc_fetch_authtoken(auth_code, rdata={
            'scope': SONADOR_SERVICE_OPENID_SCOPE,
        })

        # Stash token so that it is available for SecurityManager to use.
        flask_session['sonador-authtoken'] = _sonador_authtoken = _token.get('token')
        flask_session['sonador-authtoken-type'] = _sonador_authtoken_type = _token.get('token_type')

        # Retrieve user info and map to Airflow user record/role, map user role based on permissions in Sonador
        _user = sonador_authtoken2user(self.appbuilder.sm, _sonador_authtoken_type, _sonador_authtoken)

        # Authenticate user to Airflow
        flask_login_user(_user, remember=False)
        return flask_redirect(self.appbuilder.get_url_for_index)


class SonadorSecurityManager(FabAirflowSecurityManagerOverride):
    ''' Sonador / Airflow Single Sign On Security Manager.
    '''
    authoauthview = SonadorAuthOAuthView
    sonador_authtoken_types = (OAUTH_TOKEN_TYPE_BEARER, API_ACCESS_TOKEN)

    def auth_user_db(self, username, password, **kwargs):
        ''' Authenticate the user to the database. For Sonador API tokens, retrieve user details
            via the data service and initializse a user instance.
        '''
        # Attempt to retrieve user via Sonador auth token
        if username in self.sonador_authtoken_types:
            _user = sonador_authtoken2user(self, username, password)

            # Login user
            if _user:
                return _user
        
        # If unable to authenticate via Sonador token, attempt to authenticate via
        # Airflow user database
        return super().auth_user_db(username, password, **kwargs)


class SonadorFabAuthManager(FabAuthManager):
    """ Extends FAB AuthManager so FastAPI token minting can accept Sonador tokens directly.
        JWT remains the on-wire token for /api/v2/* to allow for the Airflow UI and internal
        API to work without modification.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sonador_conn = SONADOR_CONN
        self.dataservice = SONADOR_DATA_SERVICE

    @cached_property
    def security_manager(self):        
        security_manager = super().security_manager
        if not isinstance(security_manager, SonadorSecurityManager):
            raise ConfigurationError(('Invalid security manager instance. To use %s, the security manager '
                + 'must be an instance of %s') % (type(self).__name__, SonadorSecurityManager.__name__))
        
        logger.info('SECURITY MANAGER: %s' % type(security_manager).__name__)
        return security_manager