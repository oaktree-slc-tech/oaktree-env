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

from sonador_auth import SONADOR_CONN, SONADOR_DATA_SERVICE, SONADOR_SERVICE_OPENID_SCOPE, \
    SonadorSecurityManager

logger = logging.getLogger(__name__)


# Sonador Connection Parameters



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
