"""Database model definitions for the 'users' app."""

import datetime

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.db.utils import IntegrityError
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

import structlog
from allauth.account.models import EmailAddress
from rest_framework.authtoken.models import Token as AuthToken

import InvenTree.cache
import InvenTree.helpers
import InvenTree.models
from common.settings import get_global_setting
from InvenTree.ready import canAppAccessDatabase, isImportingData

logger = structlog.get_logger('inventree')


#  OVERRIDE START
# Overrides Django User model __str__ with a custom function to be able to change
# string representation of a user
def user_model_str(self):
    """Function to override the default Django User __str__."""
    if get_global_setting('DISPLAY_FULL_NAMES', cache=True):
        if self.first_name or self.last_name:
            return f'{self.first_name} {self.last_name}'
    return self.username


User.add_to_class('__str__', user_model_str)  # Overriding User.__str__
#  OVERRIDE END


if settings.LDAP_AUTH:
    from django_auth_ldap.backend import populate_user

    @receiver(populate_user)
    def create_email_address(user, **kwargs):
        """If a django user is from LDAP and has an email attached to it, create an allauth email address for them automatically.

        https://django-auth-ldap.readthedocs.io/en/latest/users.html#populating-users
        https://django-auth-ldap.readthedocs.io/en/latest/reference.html#django_auth_ldap.backend.populate_user
        """
        # User must exist in the database before we can create their EmailAddress. By their recommendation,
        # we can just call .save() now
        user.save()

        # if they got an email address from LDAP, create it now and make it the primary
        if user.email:
            EmailAddress.objects.create(user=user, email=user.email, primary=True)


def default_token():
    """Generate a default value for the token."""
    return ApiToken.generate_key()


def default_token_expiry():
    """Generate an expiry date for a newly created token."""
    return InvenTree.helpers.current_date() + datetime.timedelta(days=365)


def default_create_token(token_model, user, serializer):
    """Generate a default value for the token."""
    token = token_model.objects.filter(user=user, name='', revoked=False)

    if token.exists():
        return token.first()

    else:
        return token_model.objects.create(user=user, name='')


class ApiToken(AuthToken, InvenTree.models.MetadataMixin):
    """Extends the default token model provided by djangorestframework.authtoken.

    Extensions:
    - Adds an 'expiry' date - tokens can be set to expire after a certain date
    - Adds a 'name' field - tokens can be given a custom name (in addition to the user information)
    """

    class Meta:
        """Metaclass defines model properties."""

        verbose_name = _('API Token')
        verbose_name_plural = _('API Tokens')
        abstract = False

    def __str__(self):
        """String representation uses the redacted token."""
        return self.token

    @classmethod
    def generate_key(cls, prefix='inv-'):
        """Generate a new token key - with custom prefix."""
        # Suffix is the date of creation
        suffix = '-' + str(datetime.datetime.now().date().isoformat().replace('-', ''))

        return prefix + str(AuthToken.generate_key()) + suffix

    # Override the 'key' field - force it to be unique
    key = models.CharField(
        default=default_token,
        verbose_name=_('Key'),
        db_index=True,
        unique=True,
        max_length=100,
        validators=[MinLengthValidator(50)],
    )

    # Override the 'user' field, to allow multiple tokens per user
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name=_('User'),
        related_name='api_tokens',
    )

    name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Token Name'),
        help_text=_('Custom token name'),
    )

    expiry = models.DateField(
        default=default_token_expiry,
        verbose_name=_('Expiry Date'),
        help_text=_('Token expiry date'),
        auto_now=False,
        auto_now_add=False,
    )

    last_seen = models.DateField(
        blank=True,
        null=True,
        verbose_name=_('Last Seen'),
        help_text=_('Last time the token was used'),
    )

    revoked = models.BooleanField(
        default=False, verbose_name=_('Revoked'), help_text=_('Token has been revoked')
    )

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Sanitize the provide name value."""
        name = str(name).strip()

        # Remove any non-printable chars
        name = InvenTree.helpers.remove_non_printable_characters(
            name, remove_newline=True
        )
        name = InvenTree.helpers.strip_html_tags(name)

        name = name.replace(' ', '-')
        # Limit to 100 characters
        name = name[:100]

        return name

    @property
    @admin.display(description=_('Token'))
    def token(self) -> str:
        """Provide a redacted version of the token.

        The *raw* key value should never be displayed anywhere!
        """
        # If the token has not yet been saved, return the raw key
        if self.pk is None:
            return self.key  # pragma: no cover

        M = len(self.key) - 20

        return self.key[:8] + '*' * M + self.key[-12:]

    @property
    @admin.display(boolean=True, description=_('Expired'))
    def expired(self) -> bool:
        """Test if this token has expired."""
        return (
            self.expiry is not None and self.expiry < InvenTree.helpers.current_date()
        )

    @property
    @admin.display(boolean=True, description=_('Active'))
    def active(self) -> bool:
        """Test if this token is active."""
        return not self.revoked and not self.expired


class RuleSet(models.Model):
    """A RuleSet is somewhat like a superset of the django permission class, in that in encapsulates a bunch of permissions.

    There are *many* apps models used within InvenTree,
    so it makes sense to group them into "roles".

    These roles translate (roughly) to the menu options available.

    Each role controls permissions for a number of database tables,
    which are then handled using the normal django permissions approach.
    """

    RULESET_CHOICES = [
        ('admin', _('Admin')),
        ('part_category', _('Part Categories')),
        ('part', _('Parts')),
        ('stocktake', _('Stocktake')),
        ('stock_location', _('Stock Locations')),
        ('stock', _('Stock Items')),
        ('build', _('Build Orders')),
        ('purchase_order', _('Purchase Orders')),
        ('sales_order', _('Sales Orders')),
        ('return_order', _('Return Orders')),
    ]

    RULESET_NAMES = [choice[0] for choice in RULESET_CHOICES]

    RULESET_PERMISSIONS = ['view', 'add', 'change', 'delete']

    @staticmethod
    def get_ruleset_models():
        """Return a dictionary of models associated with each ruleset."""
        ruleset_models = {
            'admin': [
                'auth_group',
                'auth_user',
                'auth_permission',
                'users_apitoken',
                'users_ruleset',
                'report_labeltemplate',
                'report_reportasset',
                'report_reportsnippet',
                'report_reporttemplate',
                'account_emailaddress',
                'account_emailconfirmation',
                'socialaccount_socialaccount',
                'socialaccount_socialapp',
                'socialaccount_socialtoken',
                'otp_totp_totpdevice',
                'otp_static_statictoken',
                'otp_static_staticdevice',
                'mfa_authenticator',
                'plugin_pluginconfig',
                'plugin_pluginsetting',
                'plugin_notificationusersetting',
                'common_barcodescanresult',
                'common_newsfeedentry',
                'taggit_tag',
                'taggit_taggeditem',
                'flags_flagstate',
                'machine_machineconfig',
                'machine_machinesetting',
            ],
            'part_category': [
                'part_partcategory',
                'part_partcategoryparametertemplate',
                'part_partcategorystar',
            ],
            'part': [
                'part_part',
                'part_partpricing',
                'part_bomitem',
                'part_bomitemsubstitute',
                'part_partsellpricebreak',
                'part_partinternalpricebreak',
                'part_parttesttemplate',
                'part_partparametertemplate',
                'part_partparameter',
                'part_partrelated',
                'part_partstar',
                'part_partcategorystar',
                'company_supplierpart',
                'company_manufacturerpart',
                'company_manufacturerpartparameter',
            ],
            'stocktake': ['part_partstocktake', 'part_partstocktakereport'],
            'stock_location': ['stock_stocklocation', 'stock_stocklocationtype'],
            'stock': [
                'stock_stockitem',
                'stock_stockitemtracking',
                'stock_stockitemtestresult',
            ],
            'build': [
                'part_part',
                'part_partcategory',
                'part_bomitem',
                'part_bomitemsubstitute',
                'build_build',
                'build_builditem',
                'build_buildline',
                'stock_stockitem',
                'stock_stocklocation',
            ],
            'purchase_order': [
                'company_company',
                'company_contact',
                'company_address',
                'company_manufacturerpart',
                'company_manufacturerpartparameter',
                'company_supplierpart',
                'company_supplierpricebreak',
                'order_purchaseorder',
                'order_purchaseorderlineitem',
                'order_purchaseorderextraline',
            ],
            'sales_order': [
                'company_company',
                'company_contact',
                'company_address',
                'order_salesorder',
                'order_salesorderallocation',
                'order_salesorderlineitem',
                'order_salesorderextraline',
                'order_salesordershipment',
            ],
            'return_order': [
                'company_company',
                'company_contact',
                'company_address',
                'order_returnorder',
                'order_returnorderlineitem',
                'order_returnorderextraline',
            ],
        }

        if settings.SITE_MULTI:
            ruleset_models['admin'].append('sites_site')

        return ruleset_models

    # Database models we ignore permission sets for
    @staticmethod
    def get_ruleset_ignore():
        """Return a list of database tables which do not require permissions."""
        return [
            # Core django models (not user configurable)
            'admin_logentry',
            'contenttypes_contenttype',
            # Models which currently do not require permissions
            'common_attachment',
            'common_customunit',
            'common_dataoutput',
            'common_inventreesetting',
            'common_inventreeusersetting',
            'common_notificationentry',
            'common_notificationmessage',
            'common_notesimage',
            'common_projectcode',
            'common_webhookendpoint',
            'common_webhookmessage',
            'common_inventreecustomuserstatemodel',
            'common_selectionlistentry',
            'common_selectionlist',
            'users_owner',
            'users_userprofile',  # User profile is handled in the serializer - only own user can change
            # Third-party tables
            'error_report_error',
            'exchange_rate',
            'exchange_exchangebackend',
            'usersessions_usersession',
            'sessions_session',
            # Django-q
            'django_q_ormq',
            'django_q_failure',
            'django_q_task',
            'django_q_schedule',
            'django_q_success',
            # Importing
            'importer_dataimportsession',
            'importer_dataimportcolumnmap',
            'importer_dataimportrow',
        ]

    RULESET_CHANGE_INHERIT = [('part', 'partparameter'), ('part', 'bomitem')]

    RULE_OPTIONS = ['can_view', 'can_add', 'can_change', 'can_delete']

    class Meta:
        """Metaclass defines additional model properties."""

        unique_together = (('name', 'group'),)

    name = models.CharField(
        max_length=50,
        choices=RULESET_CHOICES,
        blank=False,
        help_text=_('Permission set'),
    )

    group = models.ForeignKey(
        Group,
        related_name='rule_sets',
        blank=False,
        null=False,
        on_delete=models.CASCADE,
        help_text=_('Group'),
    )

    can_view = models.BooleanField(
        verbose_name=_('View'), default=False, help_text=_('Permission to view items')
    )

    can_add = models.BooleanField(
        verbose_name=_('Add'), default=False, help_text=_('Permission to add items')
    )

    can_change = models.BooleanField(
        verbose_name=_('Change'),
        default=False,
        help_text=_('Permissions to edit items'),
    )

    can_delete = models.BooleanField(
        verbose_name=_('Delete'),
        default=False,
        help_text=_('Permission to delete items'),
    )

    @classmethod
    def check_table_permission(cls, user: User, table, permission):
        """Check if the provided user has the specified permission against the table."""
        # Superuser knows no bounds
        if user.is_superuser:
            return True

        # If the table does *not* require permissions
        if table in cls.get_ruleset_ignore():
            return True

        # Work out which roles touch the given table
        for role in cls.RULESET_NAMES:
            if table in cls.get_ruleset_models()[role]:
                if check_user_role(user, role, permission):
                    return True

        # Check for children models which inherits from parent role
        for parent, child in cls.RULESET_CHANGE_INHERIT:
            # Get child model name
            parent_child_string = f'{parent}_{child}'

            if parent_child_string == table:
                # Check if parent role has change permission
                if check_user_role(user, parent, 'change'):
                    return True

        # Print message instead of throwing an error
        name = getattr(user, 'name', user.pk)
        logger.debug(
            "User '%s' failed permission check for %s.%s", name, table, permission
        )

        return False

    @staticmethod
    def get_model_permission_string(model, permission):
        """Construct the correctly formatted permission string, given the app_model name, and the permission type."""
        model, app = split_model(model)

        return f'{app}.{permission}_{model}'

    def __str__(self, debug=False):  # pragma: no cover
        """Ruleset string representation."""
        if debug:
            # Makes debugging easier
            return (
                f'{str(self.group).ljust(15)}: {self.name.title().ljust(15)} | '
                f'v: {str(self.can_view).ljust(5)} | a: {str(self.can_add).ljust(5)} | '
                f'c: {str(self.can_change).ljust(5)} | d: {str(self.can_delete).ljust(5)}'
            )
        return self.name

    def save(self, *args, **kwargs):
        """Intercept the 'save' functionality to make additional permission changes.

        It does not make sense to be able to change / create something,
        but not be able to view it!
        """
        if self.can_add or self.can_change or self.can_delete:
            self.can_view = True

        if self.can_add or self.can_delete:
            self.can_change = True

        super().save(*args, **kwargs)

        if self.group:
            # Update the group too!
            self.group.save()

    def get_models(self):
        """Return the database tables / models that this ruleset covers."""
        return self.get_ruleset_models().get(self.name, [])


def split_model(model):
    """Get modelname and app from modelstring."""
    *app, model = model.split('_')

    # handle models that have
    app = '_'.join(app) if len(app) > 1 else app[0]

    return model, app


def split_permission(app, perm):
    """Split permission string into permission and model."""
    permission_name, *model = perm.split('_')
    # handle models that have underscores
    if len(model) > 1:  # pragma: no cover
        app += '_' + '_'.join(model[:-1])
        perm = permission_name + '_' + model[-1:][0]
    model = model[-1:][0]
    return perm, model


def update_group_roles(group, debug=False):
    """Iterates through all of the RuleSets associated with the group, and ensures that the correct permissions are either applied or removed from the group.

    This function is called under the following conditions:

    a) Whenever the InvenTree database is launched
    b) Whenever the group object is updated

    The RuleSet model has complete control over the permissions applied to any group.
    """
    if not canAppAccessDatabase(allow_test=True):
        return  # pragma: no cover

    # List of permissions already associated with this group
    group_permissions = set()

    # Iterate through each permission already assigned to this group,
    # and create a simplified permission key string
    for p in group.permissions.all().prefetch_related('content_type'):
        (permission, app, model) = p.natural_key()
        permission_string = f'{app}.{permission}'
        group_permissions.add(permission_string)

    # List of permissions which must be added to the group
    permissions_to_add = set()

    # List of permissions which must be removed from the group
    permissions_to_delete = set()

    def add_model(name, action, allowed):
        """Add a new model to the pile.

        Args:
            name: The name of the model e.g. part_part
            action: The permission action e.g. view
            allowed: Whether or not the action is allowed
        """
        if action not in ['view', 'add', 'change', 'delete']:  # pragma: no cover
            raise ValueError(f'Action {action} is invalid')

        permission_string = RuleSet.get_model_permission_string(model, action)

        if allowed:
            # An 'allowed' action is always preferenced over a 'forbidden' action
            if permission_string in permissions_to_delete:
                permissions_to_delete.remove(permission_string)

            permissions_to_add.add(permission_string)

        elif permission_string not in permissions_to_add:
            permissions_to_delete.add(permission_string)

    # Pre-fetch all the RuleSet objects
    rulesets = {
        r.name: r for r in RuleSet.objects.filter(group=group).prefetch_related('group')
    }

    # Get all the rulesets associated with this group
    for r in RuleSet.RULESET_CHOICES:
        rulename = r[0]

        if rulename in rulesets:
            ruleset = rulesets[rulename]
        else:
            try:
                ruleset = RuleSet.objects.get(group=group, name=rulename)
            except RuleSet.DoesNotExist:
                ruleset = RuleSet.objects.create(group=group, name=rulename)

        # Which database tables does this RuleSet touch?
        models = ruleset.get_models()

        for model in models:
            # Keep track of the available permissions for each model

            add_model(model, 'view', ruleset.can_view)
            add_model(model, 'add', ruleset.can_add)
            add_model(model, 'change', ruleset.can_change)
            add_model(model, 'delete', ruleset.can_delete)

    def get_permission_object(permission_string):
        """Find the permission object in the database, from the simplified permission string.

        Args:
            permission_string: a simplified permission_string e.g. 'part.view_partcategory'

        Returns the permission object in the database associated with the permission string
        """
        (app, perm) = permission_string.split('.')

        perm, model = split_permission(app, perm)

        try:
            content_type = ContentType.objects.get(app_label=app, model=model)
            permission = Permission.objects.get(
                content_type=content_type, codename=perm
            )
        except ContentType.DoesNotExist:  # pragma: no cover
            # logger.warning(
            #     "Error: Could not find permission matching '%s'", permission_string
            # )
            permission = None

        return permission

    # Add any required permissions to the group
    for perm in permissions_to_add:
        # Ignore if permission is already in the group
        if perm in group_permissions:
            continue

        permission = get_permission_object(perm)

        if permission:
            group.permissions.add(permission)

        if debug:  # pragma: no cover
            logger.debug('Adding permission %s to group %s', perm, group.name)

    # Remove any extra permissions from the group
    for perm in permissions_to_delete:
        # Ignore if the permission is not already assigned
        if perm not in group_permissions:
            continue

        permission = get_permission_object(perm)

        if permission:
            group.permissions.remove(permission)

        if debug:  # pragma: no cover
            logger.debug('Removing permission %s from group %s', perm, group.name)

    # Enable all action permissions for certain children models
    # if parent model has 'change' permission
    for parent, child in RuleSet.RULESET_CHANGE_INHERIT:
        parent_child_string = f'{parent}_{child}'

        # Check each type of permission
        for action in ['view', 'change', 'add', 'delete']:
            parent_perm = f'{parent}.{action}_{parent}'

            if parent_perm in group_permissions:
                child_perm = f'{parent}.{action}_{child}'

                # Check if child permission not already in group
                if child_perm not in group_permissions:
                    # Create permission object
                    add_model(parent_child_string, action, ruleset.can_delete)
                    # Add to group
                    permission = get_permission_object(child_perm)
                    if permission:
                        group.permissions.add(permission)
                        logger.debug(
                            'Adding permission %s to group %s', child_perm, group.name
                        )


def check_user_permission(user: User, model: models.Model, permission: str) -> bool:
    """Check if the user has a particular permission against a given model type.

    Arguments:
        user: The user object to check
        model: The model class to check (e.g. 'part')
        permission: The permission to check (e.g. 'view' / 'delete')

    Returns:
        bool: True if the user has the specified permission
    """
    if not user:
        return False

    if user.is_superuser:
        return True

    permission_name = f'{model._meta.app_label}.{permission}_{model._meta.model_name}'
    return user.has_perm(permission_name)


def check_user_role(user: User, role: str, permission: str) -> bool:
    """Check if a user has a particular role:permission combination.

    Arguments:
        user: The user object to check
        role: The role to check (e.g. 'part' / 'stock')
        permission: The permission to check (e.g. 'view' / 'delete')

    Returns:
        bool: True if the user has the specified role:permission combination
    """
    if not user:
        return False

    if user.is_superuser:
        return True

    # First, check the session cache
    cache_key = f'role_{user.pk}_{role}_{permission}'
    result = InvenTree.cache.get_session_cache(cache_key)

    if result is not None:
        return result

    # Default for no match
    result = False

    for group in user.groups.all():
        for rule in group.rule_sets.all():
            if rule.name == role:
                if permission == 'add' and rule.can_add:
                    result = True
                    break

                if permission == 'change' and rule.can_change:
                    result = True
                    break

                if permission == 'view' and rule.can_view:
                    result = True
                    break

                if permission == 'delete' and rule.can_delete:
                    result = True
                    break

    # Save result to session-cache
    InvenTree.cache.set_session_cache(cache_key, result)

    return result


class Owner(models.Model):
    """The Owner class is a proxy for a Group or User instance.

    Owner can be associated to any InvenTree model (part, stock, build, etc.)

    owner_type: Model type (Group or User)
    owner_id: Group or User instance primary key
    owner: Returns the Group or User instance combining the owner_type and owner_id fields
    """

    class Meta:
        """Metaclass defines extra model properties."""

        # Ensure all owners are unique
        constraints = [
            UniqueConstraint(fields=['owner_type', 'owner_id'], name='unique_owner')
        ]

    @classmethod
    def get_owners_matching_user(cls, user):
        """Return all "owner" objects matching the provided user.

        Includes:
        - An exact match for the user
        - Any groups that the user is a part of
        """
        user_type = ContentType.objects.get(app_label='auth', model='user')
        group_type = ContentType.objects.get(app_label='auth', model='group')

        owners = []

        try:
            owners.append(cls.objects.get(owner_id=user.pk, owner_type=user_type))
        except Exception:  # pragma: no cover
            pass

        for group in user.groups.all():
            try:
                owner = cls.objects.get(owner_id=group.pk, owner_type=group_type)
                owners.append(owner)
            except Exception:  # pragma: no cover
                pass

        return owners

    @staticmethod
    def get_api_url():  # pragma: no cover
        """Returns the API endpoint URL associated with the Owner model."""
        return reverse('api-owner-list')

    owner_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )

    owner_id = models.PositiveIntegerField(null=True, blank=True)

    owner = GenericForeignKey('owner_type', 'owner_id')

    def __str__(self):
        """Defines the owner string representation."""
        if self.owner_type.name == 'user' and get_global_setting(
            'DISPLAY_FULL_NAMES', cache=True
        ):
            display_name = self.owner.get_full_name()
        else:
            display_name = str(self.owner)
        return f'{display_name} ({self.owner_type.name})'

    def name(self):
        """Return the 'name' of this owner."""
        if self.owner_type.name == 'user' and get_global_setting(
            'DISPLAY_FULL_NAMES', cache=True
        ):
            return self.owner.get_full_name() or str(self.owner)
        return str(self.owner)

    def label(self):
        """Return the 'type' label of this owner i.e. 'user' or 'group'."""
        return str(self.owner_type.name)

    @classmethod
    def create(cls, obj):
        """Check if owner exist then create new owner entry."""
        # Check for existing owner
        existing_owner = cls.get_owner(obj)

        if not existing_owner:
            # Create new owner
            try:
                return cls.objects.create(owner=obj)
            except IntegrityError:  # pragma: no cover
                return None

        return existing_owner

    @classmethod
    def get_owner(cls, user_or_group):
        """Get owner instance for a group or user."""
        user_model = get_user_model()
        owner = None
        content_type_id = 0
        content_type_id_list = [
            ContentType.objects.get_for_model(Group).id,
            ContentType.objects.get_for_model(user_model).id,
        ]

        # If instance type is obvious: set content type
        if isinstance(user_or_group, Group):
            content_type_id = content_type_id_list[0]
        elif isinstance(user_or_group, get_user_model()):
            content_type_id = content_type_id_list[1]

        if content_type_id:
            try:
                owner = Owner.objects.get(
                    owner_id=user_or_group.id, owner_type=content_type_id
                )
            except Owner.DoesNotExist:
                pass

        return owner

    def get_related_owners(self, include_group=False):
        """Get all owners "related" to an owner.

        This method is useful to retrieve all "user-type" owners linked to a "group-type" owner
        """
        user_model = get_user_model()
        related_owners = None

        if type(self.owner) is Group:
            users = user_model.objects.filter(groups__name=self.owner.name)

            if include_group:
                # Include "group-type" owner in the query
                query = Q(
                    owner_id__in=users,
                    owner_type=ContentType.objects.get_for_model(user_model).id,
                ) | Q(
                    owner_id=self.owner.id,
                    owner_type=ContentType.objects.get_for_model(Group).id,
                )
            else:
                query = Q(
                    owner_id__in=users,
                    owner_type=ContentType.objects.get_for_model(user_model).id,
                )

            related_owners = Owner.objects.filter(query)

        elif type(self.owner) is user_model:
            related_owners = [self]

        return related_owners

    def is_user_allowed(self, user, include_group: bool = False):
        """Check if user is allowed to access something owned by this owner."""
        user_owner = Owner.get_owner(user)
        return user_owner in self.get_related_owners(include_group=include_group)


@receiver(post_save, sender=Group, dispatch_uid='create_owner')
@receiver(post_save, sender=get_user_model(), dispatch_uid='create_owner')
def create_owner(sender, instance, **kwargs):
    """Callback function to create a new owner instance after either a new group or user instance is saved."""
    # Ignore during data import process to avoid data duplication
    if not isImportingData():
        Owner.create(obj=instance)


@receiver(post_delete, sender=Group, dispatch_uid='delete_owner')
@receiver(post_delete, sender=get_user_model(), dispatch_uid='delete_owner')
def delete_owner(sender, instance, **kwargs):
    """Callback function to delete an owner instance after either a new group or user instance is deleted."""
    owner = Owner.get_owner(instance)
    owner.delete()


@receiver(post_save, sender=Group, dispatch_uid='create_missing_rule_sets')
def create_missing_rule_sets(sender, instance, **kwargs):
    """Called *after* a Group object is saved.

    As the linked RuleSet instances are saved *before* the Group, then we can now use these RuleSet values to update the group permissions.
    """
    update_group_roles(instance)


class UserProfile(InvenTree.models.MetadataMixin):
    """Model to store additional user profile information."""

    class UserType(models.TextChoices):
        """Enumeration for user types."""

        BOT = 'bot', _('Bot')
        INTERNAL = 'internal', _('Internal')
        EXTERNAL = 'external', _('External')
        GUEST = 'guest', _('Guest')

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='profile', verbose_name=_('User')
    )
    language = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name=_('Language'),
        help_text=_('Preferred language for the user'),
    )
    theme = models.JSONField(
        blank=True,
        null=True,
        verbose_name=_('Theme'),
        help_text=_('Settings for the web UI as JSON - do not edit manually!'),
    )
    widgets = models.JSONField(
        blank=True,
        null=True,
        verbose_name=_('Widgets'),
        help_text=_(
            'Settings for the dashboard widgets as JSON - do not edit manually!'
        ),
    )
    displayname = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Display Name'),
        help_text=_('Chosen display name for the user'),
    )
    position = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Position'),
        help_text=_('Main job title or position'),
    )
    status = models.CharField(
        max_length=2000,
        blank=True,
        null=True,
        verbose_name=_('Status'),
        help_text=_('User status message'),
    )
    location = models.CharField(
        max_length=2000,
        blank=True,
        null=True,
        verbose_name=_('Location'),
        help_text=_('User location information'),
    )
    active = models.BooleanField(
        default=True,
        verbose_name=_('Active'),
        help_text=_('User is actively using the system'),
    )
    contact = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Contact'),
        help_text=_('Preferred contact information for the user'),
    )
    type = models.CharField(
        max_length=10,
        choices=UserType.choices,
        default=UserType.INTERNAL,
        verbose_name=_('Type'),
        help_text=_('Which type of user is this?'),
    )
    organisation = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Organisation'),
        help_text=_('Users primary organisation/affiliation'),
    )
    primary_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_users',
        verbose_name=_('Primary Group'),
        help_text=_('Primary group for the user'),
    )

    def __str__(self):
        """Return string representation of the user profile."""
        return f'{self.user.username} user profile'

    def save(self, *args, **kwargs):
        """Ensure primary_group is a group that the user is a member of."""
        if self.primary_group and self.primary_group not in self.user.groups.all():
            self.primary_group = None
        super().save(*args, **kwargs)


# Signal to create or update user profile when user is saved
@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """Create or update user profile when user is saved."""
    if created:
        UserProfile.objects.create(user=instance)
    instance.profile.save()


# Validate groups
@receiver(post_save, sender=Group)
def validate_primary_group_on_save(sender, instance, **kwargs):
    """Validate primary_group on user profiles when a group is created or updated."""
    for user in instance.user_set.all():
        profile = user.profile
        if profile.primary_group and profile.primary_group not in user.groups.all():
            profile.primary_group = None
            profile.save()


@receiver(post_delete, sender=Group)
def validate_primary_group_on_delete(sender, instance, **kwargs):
    """Validate primary_group on user profiles when a group is deleted."""
    for user in instance.user_set.all():
        profile = user.profile
        if profile.primary_group == instance:
            profile.primary_group = None
            profile.save()


@receiver(m2m_changed, sender=User.groups.through)
def validate_primary_group_on_group_change(sender, instance, action, **kwargs):
    """Validate primary_group on user profiles when a group is added or removed."""
    if action in ['post_add', 'post_remove']:
        profile = instance.profile
        if profile.primary_group and profile.primary_group not in instance.groups.all():
            profile.primary_group = None
            profile.save()
