import os, logging, json, posixpath
from typing import Any, Union

from flask import abort, request, make_response, redirect
from flask_login import login_user
from flask_appbuilder.views import expose
from flask_appbuilder.const import AUTH_OAUTH
from flask_appbuilder.security.views import AuthOAuthView
from flask import session

from airflow.configuration import conf
from airflow.providers.fab.auth_manager.security_manager.override import FabAirflowSecurityManagerOverride

from client.errors import ConfigurationError
from client.utils.object import pick
from sonador import apisettings as sonador_api
from sonador.servers import SonadorServer

logger = logging.getLogger(__name__)


# Sonador Connection Parameters
SONADOR_URL = os.environ.get(sonador_api.SONADOR_URL)
SONADOR_APITOKEN = os.environ.get(sonador_api.SONADOR_APITOKEN)
SONADOR_SERVICE_CLIENT_ID = os.environ.get(sonador_api.SONADOR_SERVICE_CLIENT_ID)
SONADOR_SERVICE_OPENID_SCOPE = os.environ.get('SONADOR_SERVICE_OPENID_SCOPE', 'openid email profile')

# Verify Sonador configuration
if not SONADOR_URL or not SONADOR_APITOKEN or not SONADOR_SERVICE_CLIENT_ID:
    raise ConfigurationError(('Invalid Sonador configuration. Missing URL, API token, or data service client ID. '
        + 'Check %s, %s, and %s environment variables.') % (
            sonador_api.SONADOR_URL, sonador_api.SONADOR_APITOKEN, sonador_api.SONADOR_SERVICE_CLIENT_ID
        ))


# Retrieve data service
SONADOR_CONN = SonadorServer(SONADOR_URL, apitoken=SONADOR_APITOKEN)
SONADOR_DATA_SERVICE = SONADOR_CONN.get_dataservice(SONADOR_SERVICE_CLIENT_ID)
if not SONADOR_DATA_SERVICE.openid_allow_auth:
    raise ConfigurationError(('Unable to enable SSO, Sonador Data Service (uid="%s") does not have OpenID Connect '
        "authentication enabled.") % (SONADOR_DATA_SERVICE.pk))


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
        auth_code = request.args.get('code')
        state = request.args.get('state')
        nonce = request.args.get('nonce')

        logger.warn('oAuth request parameters: auth-code=%s state=%s nonce=%s' % (auth_code, state, nonce))

        if not auth_code:
            raise ValueError('Invalid OpenID request structure, Unable to retrieve authorization code')

        # Exchange code for authorization token
        _token = SONADOR_DATA_SERVICE.oidc_fetch_authtoken(auth_code, rdata={
            'scope': SONADOR_SERVICE_OPENID_SCOPE,
        })

        # Stash token so that it is available for SecurityManager to use.
        session['sonador-authtoken'] = _sonador_authtoken = _token.get('token')
        session['sonador-authtoken-type'] = _sonador_authtoken_type = _token.get('token_type')

        # Retrieve user info and map to Airflow user record/role, map user role based on permissions in Sonador
        sm = self.appbuilder.sm
        user_info = SONADOR_DATA_SERVICE.verify_api_credentials(_sonador_authtoken_type, _sonador_authtoken)
        _user_data = pick(user_info['user'], ('username', 'first_name', 'last_name', 'email'))
        _user_data.update({
            'role': sm.find_role('Admin' if (
                    user_info['user'].get('is_staff') or user_info['user'].get('is_superuser')) \
                else 'User'),
        })
        _user = sm.find_user(username=_user_data.get('username'))
        if not _user:
            _user = sm.add_user(**_user_data)

        # Authenticate user to Airflow
        login_user(_user, remember=False)
        return redirect(self.appbuilder.get_url_for_index)


class SonadorSecurityManager(FabAirflowSecurityManagerOverride):
    ''' Sonador / Airflow Single Sign On Security Manager.
    '''
    authoauthview = SonadorAuthOAuthView


OAUTH_PROVIDERS = [{
    'name': 'sonador',
    'display_name': ' Sonador SSO ',
    'icon': 'fa-circle-check',
    'token_key': 'access_token',
    'remote_app': {
        'client_id': SONADOR_DATA_SERVICE.openid_client_id,         # Data Service OpenID Client ID
        'authorize_url': SONADOR_CONN.apiurl(SONADOR_DATA_SERVICE.url_oidc_authorize),   # OpenID Connect Authorization URL
        'access_token_url': SONADOR_CONN.apiurl(SONADOR_DATA_SERVICE.url_oidc_token),    # OpenID Connect Token URL
        'client_kwargs': {
            'scope': SONADOR_SERVICE_OPENID_SCOPE,
            'token_endpoint_auth_method': 'none',                   # Sonador API auth rather than OpenID mediated atuh
        }
    }
}]


# Airflow SSO configuration
SECURITY_MANAGER_CLASS = SonadorSecurityManager
AUTH_TYPE = AUTH_OAUTH
SQLALCHEMY_DATABASE_URI = conf.get("database", "SQL_ALCHEMY_CONN")  # Airflow 3+ typically uses [database]
CSRF_ENABLED = True
