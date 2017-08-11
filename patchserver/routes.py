import dateutil.parser
import hashlib

import flask
import sqlalchemy
from sqlalchemy.exc import IntegrityError

from . import app, db
from .models import (
    SoftwareTitle,
    ExtensionAttribute,
    SoftwareTitleCriteria,
    Criteria,
    Patch,
    PatchComponent,
    PatchCriteria,
    PatchKillApps
)
from .exc import SoftwareTitleNotFound


@app.route('/')
def root():
    return flask.render_template('index.html'), 200


@app.errorhandler(SoftwareTitleNotFound)
def error_title_not_found(err):
    app.logger.error(err)
    return flask.jsonify({'title_not_found': err.message}), 404


@app.errorhandler(IntegrityError)
def database_integrity_error(err):
    if 'software_titles.id_name' in err.message:
        message = 'A software title of the provided name already exists.'
    else:
        message = err.message

    return flask.jsonify({'database_conflict': message}), 409


def lookup_software_title(name_id):
    title = SoftwareTitle.query.filter_by(id_name=name_id).first()
    if not title:
        raise SoftwareTitleNotFound(name_id)
    else:
        return title


@app.route('/jamf/v1/software', methods=['GET', 'POST'])
def software_titles():
    """Endpoint for Jamf Pro"""
    if flask.request.method == 'GET':
        titles = SoftwareTitle.query.all()
        return flask.jsonify([title.serialize_short for title in titles]), 200


@app.route('/jamf/v1/software/<name_ids>')
def software_titles_select(name_ids):
    """Endpoint for Jamf Pro"""
    # Comma separated list of name IDs
    name_id_list = name_ids.split(',')
    title_list = SoftwareTitle.query.filter(
        sqlalchemy.or_(SoftwareTitle.id_name.in_(name_id_list))).all()

    not_found = [name for name in name_id_list
                 if name not in
                 [title.id_name for title in title_list]]
    if not_found:
        raise SoftwareTitleNotFound(not_found)
    else:
        return flask.jsonify(
            [title.serialize_short for title in title_list]), 200


@app.route('/jamf/v1/patch/<name_id>')
def patch_by_name_id(name_id):
    """Endpoint for Jamf Pro"""
    return flask.jsonify(lookup_software_title(name_id).serialize), 200


@app.route('/api/v1/title/create', methods=['POST'])
def title_create():
    """Create a new Patch Software Title"""
    data = flask.request.get_json()

    new_title = SoftwareTitle(
        id_name=data['id'],
        name=data['name'],
        publisher=data['publisher'],
        app_name=data['appName'],
        bundle_id=data['bundleId']
    )

    if data.get('requirements'):
        create_criteria_objects(
            reversed(data['requirements']), software_title=new_title)

    if data.get('patches'):
        create_patch_objects(
            reversed(data['patches']), software_title=new_title)

    if data.get('extensionAttributes'):
        create_extension_attributes(
            data['extensionAttributes'], new_title)

    db.session.commit()

    return flask.jsonify(
        {'id': new_title.id_name, 'database_id': new_title.id}), 201


@app.route('/api/v1/title/<name_id>/delete', methods=['DELETE'])
def title_delete(name_id):
    """Delete a Patch Software Title"""
    title = lookup_software_title(name_id)
    db.session.delete(title)
    db.session.commit()
    return flask.jsonify({}), 204


@app.route('/api/v1/title/<name_id>/requirements/add', methods=['POST'])
def title_requirements_add(name_id):
    """
    {
        "items": [
            <criteria_object>
        ]
    }
    """
    title = lookup_software_title(name_id)
    data = flask.request.get_json()

    create_criteria_objects(data['items'], software_title=title)
    return flask.jsonify({}), 201


def create_criteria_objects(criteria_list, software_title=None,
                            patch_object=None, patch_component=None):
    """
    [
        <criteria_object_1>,
        <criteria_object_2>,
        <criteria_object_3>
    ]
    """

    for criterion in criteria_list:
        criteria_hash = hashlib.sha1(
            criterion['name'] +
            criterion['operator'] +
            criterion['value'] +
            criterion['type'] +
            str(criterion.get('and', True))
        ).hexdigest()

        criteria = Criteria.query.filter_by(hash=criteria_hash).first()
        if not criteria:
            criteria = Criteria(
                name=criterion['name'],
                operator=criterion['operator'],
                value=criterion['value'],
                type_=criterion['type'],
                and_=criterion.get('and', True)
            )

        if software_title:
            db.session.add(
                SoftwareTitleCriteria(
                    software_title=software_title,
                    criteria=criteria
                )
            )
        elif patch_object:
            db.session.add(
                PatchCriteria(
                    patch=patch_object,
                    criteria=criteria
                )
            )
        elif patch_component:
            criteria.patch_component = patch_component

        db.session.add(criteria)


def create_extension_attributes(ext_att_list, software_title):
    for ext_att in ext_att_list:
        db.session.add(
            ExtensionAttribute(
                key=ext_att['key'],
                value=ext_att['value'],
                display_name=ext_att['displayName'],
                software_title=software_title
            )
        )


@app.route('/api/v1/title/<name_id>/patches')
def title_patches(name_id):
    title = lookup_software_title(name_id)
    return flask.jsonify(
        {
            'id': title.id_name,
            'patches': [patch.serialize for patch in title.patches]
        }
    ), 200


@app.route('/api/v1/title/<name_id>/patches/add', methods=['POST'])
def title_patches_add(name_id):
    """
    {
        "items": [
            <patch_object>
        ]
    }
    """
    title = lookup_software_title(name_id)
    data = flask.request.get_json()

    create_patch_objects(data['items'], software_title=title)
    db.session.commit()

    return flask.jsonify({}), 201


def create_patch_objects(patch_list, software_title):
    """"""
    for patch in patch_list:
        new_patch = Patch(
            version=patch['version'],
            release_date=dateutil.parser.parse(patch['releaseDate']),
            standalone=patch['standalone'],
            minimum_operating_system=patch['minimumOperatingSystem'],
            reboot=patch['reboot'],
            software_title=software_title
        )
        db.session.add(new_patch)

        if patch.get('capabilities'):
            create_criteria_objects(
                patch['capabilities'], patch_object=new_patch)

        if patch.get('components'):
            create_patch_object_components(
                patch['components'], patch_object=new_patch)

        if patch.get('killApps'):
            create_patch_object_kill_apps(
                patch['killApps'], patch_object=new_patch)


def create_patch_object_components(component_list, patch_object):
    for component in component_list:
        new_component = PatchComponent(
            name=component['name'],
            version=component['version'],
            patch=patch_object
        )
        db.session.add(new_component)

        if component.get('criteria'):
            create_criteria_objects(
                component['criteria'], patch_component=new_component)


def create_patch_object_kill_apps(kill_apps_list, patch_object):
    for kill_app in kill_apps_list:
        new_kill_app = PatchKillApps(
            bundleId=kill_app['bundleId'],
            appName=kill_app['appName'],
            patch=patch_object
        )
        db.session.add(new_kill_app)
