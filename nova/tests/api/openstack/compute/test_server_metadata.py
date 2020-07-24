# Copyright 2011 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import uuid

import mock
from oslo.config import cfg
from oslo.serialization import jsonutils
from oslo.utils import timeutils
import six
import webob

from nova.api.openstack.compute.plugins.v3 import server_metadata \
    as server_metadata_v21
from nova.api.openstack.compute import server_metadata as server_metadata_v2
from nova.compute import rpcapi as compute_rpcapi
from nova.compute import vm_states
import nova.db
from nova import exception
from nova import objects
from nova import test
from nova.tests.api.openstack import fakes
from nova.tests import fake_instance


CONF = cfg.CONF


def return_create_instance_metadata_max(context, server_id, metadata, delete):
    return stub_max_server_metadata()


def return_create_instance_metadata(context, server_id, metadata, delete):
    return stub_server_metadata()


def fake_instance_save(inst, **kwargs):
    inst.metadata = stub_server_metadata()
    inst.obj_reset_changes()


def return_server_metadata(context, server_id):
    if not isinstance(server_id, six.string_types) or not len(server_id) == 36:
        msg = 'id %s must be a uuid in return server metadata' % server_id
        raise Exception(msg)
    return stub_server_metadata()


def return_empty_server_metadata(context, server_id):
    return {}


def delete_server_metadata(context, server_id, key):
    pass


def stub_server_metadata():
    metadata = {
        "key1": "value1",
        "key2": "value2",
        "key3": "value3",
    }
    return metadata


def stub_max_server_metadata():
    metadata = {"metadata": {}}
    for num in range(CONF.quota_metadata_items):
        metadata['metadata']['key%i' % num] = "blah"
    return metadata


def return_server(context, server_id, columns_to_join=None):
    return fake_instance.fake_db_instance(
        **{'id': server_id,
           'uuid': '0cc3346e-9fef-4445-abe6-5d2b2690ec64',
           'name': 'fake',
           'locked': False,
           'launched_at': timeutils.utcnow(),
           'vm_state': vm_states.ACTIVE})


def return_server_by_uuid(context, server_uuid,
                          columns_to_join=None, use_subordinate=False):
    return fake_instance.fake_db_instance(
        **{'id': 1,
           'uuid': '0cc3346e-9fef-4445-abe6-5d2b2690ec64',
           'name': 'fake',
           'locked': False,
           'launched_at': timeutils.utcnow(),
           'metadata': stub_server_metadata(),
           'vm_state': vm_states.ACTIVE})


def return_server_nonexistent(context, server_id,
        columns_to_join=None, use_subordinate=False):
    raise exception.InstanceNotFound(instance_id=server_id)


def fake_change_instance_metadata(self, context, instance, diff):
    pass


class ServerMetaDataTestV21(test.TestCase):
    validation_ex = exception.ValidationError
    validation_ex_large = validation_ex

    def setUp(self):
        super(ServerMetaDataTestV21, self).setUp()
        fakes.stub_out_key_pair_funcs(self.stubs)
        self.stubs.Set(nova.db, 'instance_get', return_server)
        self.stubs.Set(nova.db, 'instance_get_by_uuid',
                       return_server_by_uuid)

        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_server_metadata)

        self.stubs.Set(compute_rpcapi.ComputeAPI, 'change_instance_metadata',
                       fake_change_instance_metadata)
        self._set_up_resources()

    def _set_up_resources(self):
        self.controller = server_metadata_v21.ServerMetadataController()
        self.uuid = str(uuid.uuid4())
        self.url = '/fake/servers/%s/metadata' % self.uuid

    def _get_request(self, param_url=''):
        return fakes.HTTPRequestV3.blank(self.url + param_url)

    def test_index(self):
        req = self._get_request()
        res_dict = self.controller.index(req, self.uuid)

        expected = {
            'metadata': {
                'key1': 'value1',
                'key2': 'value2',
                'key3': 'value3',
            },
        }
        self.assertEqual(expected, res_dict)

    def test_index_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_server_nonexistent)
        req = self._get_request()
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.index, req, self.url)

    def test_index_no_data(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_empty_server_metadata)
        req = self._get_request()
        res_dict = self.controller.index(req, self.uuid)
        expected = {'metadata': {}}
        self.assertEqual(expected, res_dict)

    def test_show(self):
        req = self._get_request('/key2')
        res_dict = self.controller.show(req, self.uuid, 'key2')
        expected = {"meta": {'key2': 'value2'}}
        self.assertEqual(expected, res_dict)

    def test_show_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_server_nonexistent)
        req = self._get_request('/key2')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.show, req, self.uuid, 'key2')

    def test_show_meta_not_found(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_empty_server_metadata)
        req = self._get_request('/key6')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.show, req, self.uuid, 'key6')

    def test_delete(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_server_metadata)
        self.stubs.Set(nova.db, 'instance_metadata_delete',
                       delete_server_metadata)
        req = self._get_request('/key2')
        req.method = 'DELETE'
        res = self.controller.delete(req, self.uuid, 'key2')

        self.assertIsNone(res)

    def test_delete_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_get_by_uuid',
                       return_server_nonexistent)
        req = self._get_request('/key1')
        req.method = 'DELETE'
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.delete, req, self.uuid, 'key1')

    def test_delete_meta_not_found(self):
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_empty_server_metadata)
        req = self._get_request('/key6')
        req.method = 'DELETE'
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.delete, req, self.uuid, 'key6')

    def test_create(self):
        self.stubs.Set(objects.Instance, 'save', fake_instance_save)
        req = self._get_request()
        req.method = 'POST'
        req.content_type = "application/json"
        body = {"metadata": {"key9": "value9"}}
        req.body = jsonutils.dumps(body)
        res_dict = self.controller.create(req, self.uuid, body=body)

        body['metadata'].update({
            "key1": "value1",
            "key2": "value2",
            "key3": "value3",
        })
        self.assertEqual(body, res_dict)

    def test_create_empty_body(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'POST'
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=None)

    def test_create_item_empty_key(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"metadata": {"": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=body)

    def test_create_item_non_dict(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"metadata": None}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=body)

    def test_create_item_key_too_long(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"metadata": {("a" * 260): "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex_large,
                          self.controller.create,
                          req, self.uuid, body=body)

    def test_create_malformed_container(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url + '/key1')
        req.method = 'PUT'
        body = {"meta": {}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=body)

    def test_create_malformed_data(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url + '/key1')
        req.method = 'PUT'
        body = {"metadata": ['asdf']}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=body)

    def test_create_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_get_by_uuid',
                       return_server_nonexistent)
        req = self._get_request()
        req.method = 'POST'
        body = {"metadata": {"key1": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.create, req, self.uuid, body=body)

    def test_update_metadata(self):
        self.stubs.Set(objects.Instance, 'save', fake_instance_save)
        req = self._get_request()
        req.method = 'POST'
        req.content_type = 'application/json'
        expected = {
            'metadata': {
                'key1': 'updatedvalue',
                'key29': 'newkey',
            }
        }
        req.body = jsonutils.dumps(expected)
        response = self.controller.update_all(req, self.uuid, body=expected)
        self.assertEqual(expected, response)

    def test_update_all(self):
        self.stubs.Set(objects.Instance, 'save', fake_instance_save)
        req = self._get_request()
        req.method = 'PUT'
        req.content_type = "application/json"
        expected = {
            'metadata': {
                'key10': 'value10',
                'key99': 'value99',
            },
        }
        req.body = jsonutils.dumps(expected)
        res_dict = self.controller.update_all(req, self.uuid, body=expected)

        self.assertEqual(expected, res_dict)

    def test_update_all_empty_container(self):
        self.stubs.Set(objects.Instance, 'save', fake_instance_save)
        req = self._get_request()
        req.method = 'PUT'
        req.content_type = "application/json"
        expected = {'metadata': {}}
        req.body = jsonutils.dumps(expected)
        res_dict = self.controller.update_all(req, self.uuid, body=expected)

        self.assertEqual(expected, res_dict)

    def test_update_all_empty_body_item(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url + '/key1')
        req.method = 'PUT'
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update_all, req, self.uuid,
                          body=None)

    def test_update_all_with_non_dict_item(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url + '/bad')
        req.method = 'PUT'
        body = {"metadata": None}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update_all, req, self.uuid,
                          body=body)

    def test_update_all_malformed_container(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'PUT'
        req.content_type = "application/json"
        expected = {'meta': {}}
        req.body = jsonutils.dumps(expected)

        self.assertRaises(self.validation_ex,
                          self.controller.update_all, req, self.uuid,
                          body=expected)

    def test_update_all_malformed_data(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'PUT'
        req.content_type = "application/json"
        expected = {'metadata': ['asdf']}
        req.body = jsonutils.dumps(expected)

        self.assertRaises(self.validation_ex,
                          self.controller.update_all, req, self.uuid,
                          body=expected)

    def test_update_all_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_get', return_server_nonexistent)
        req = self._get_request()
        req.method = 'PUT'
        req.content_type = "application/json"
        body = {'metadata': {'key10': 'value10'}}
        req.body = jsonutils.dumps(body)

        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update_all, req, '100', body=body)

    def test_update_all_non_dict(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'PUT'
        body = {"metadata": None}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex, self.controller.update_all,
                          req, self.uuid, body=body)

    def test_update_item(self):
        self.stubs.Set(objects.Instance, 'save', fake_instance_save)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {"key1": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"
        res_dict = self.controller.update(req, self.uuid, 'key1', body=body)
        expected = {"meta": {'key1': 'value1'}}
        self.assertEqual(expected, res_dict)

    def test_update_item_nonexistent_server(self):
        self.stubs.Set(nova.db, 'instance_get_by_uuid',
                       return_server_nonexistent)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {"key1": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update, req, self.uuid, 'key1',
                          body=body)

    def test_update_item_empty_body(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'key1',
                          body=None)

    def test_update_malformed_container(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url)
        req.method = 'PUT'
        expected = {'meta': {}}
        req.body = jsonutils.dumps(expected)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'key1',
                          body=expected)

    def test_update_malformed_data(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url)
        req.method = 'PUT'
        expected = {'metadata': ['asdf']}
        req.body = jsonutils.dumps(expected)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'key1',
                          body=expected)

    def test_update_item_empty_key(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {"": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, '',
                          body=body)

    def test_update_item_key_too_long(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {("a" * 260): "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex_large,
                          self.controller.update,
                          req, self.uuid, ("a" * 260), body=body)

    def test_update_item_value_too_long(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {"key1": ("a" * 260)}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex_large,
                          self.controller.update,
                          req, self.uuid, "key1", body=body)

    def test_update_item_too_many_keys(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/key1')
        req.method = 'PUT'
        body = {"meta": {"key1": "value1", "key2": "value2"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'key1',
                          body=body)

    def test_update_item_body_uri_mismatch(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/bad')
        req.method = 'PUT'
        body = {"meta": {"key1": "value1"}}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.update, req, self.uuid, 'bad',
                          body=body)

    def test_update_item_non_dict(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request('/bad')
        req.method = 'PUT'
        body = {"meta": None}
        req.body = jsonutils.dumps(body)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'bad',
                          body=body)

    def test_update_empty_container(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = fakes.HTTPRequest.blank(self.url)
        req.method = 'PUT'
        expected = {'metadata': {}}
        req.body = jsonutils.dumps(expected)
        req.headers["content-type"] = "application/json"

        self.assertRaises(self.validation_ex,
                          self.controller.update, req, self.uuid, 'bad',
                          body=expected)

    def test_too_many_metadata_items_on_create(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        data = {"metadata": {}}
        for num in range(CONF.quota_metadata_items + 1):
            data['metadata']['key%i' % num] = "blah"
        req = self._get_request()
        req.method = 'POST'
        req.body = jsonutils.dumps(data)
        req.headers["content-type"] = "application/json"

        self.assertRaises(webob.exc.HTTPForbidden,
                          self.controller.create, req, self.uuid, body=data)

    def test_invalid_metadata_items_on_create(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'POST'
        req.headers["content-type"] = "application/json"

        # test for long key
        data = {"metadata": {"a" * 260: "value1"}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex_large,
                          self.controller.create, req, self.uuid, body=data)

        # test for long value
        data = {"metadata": {"key": "v" * 260}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex_large,
                          self.controller.create, req, self.uuid, body=data)

        # test for empty key.
        data = {"metadata": {"": "value1"}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex,
                          self.controller.create, req, self.uuid, body=data)

    def test_too_many_metadata_items_on_update_item(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        data = {"metadata": {}}
        for num in range(CONF.quota_metadata_items + 1):
            data['metadata']['key%i' % num] = "blah"
        req = self._get_request()
        req.method = 'PUT'
        req.body = jsonutils.dumps(data)
        req.headers["content-type"] = "application/json"

        self.assertRaises(webob.exc.HTTPForbidden, self.controller.update_all,
                          req, self.uuid, body=data)

    def test_invalid_metadata_items_on_update_item(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        data = {"metadata": {}}
        for num in range(CONF.quota_metadata_items + 1):
            data['metadata']['key%i' % num] = "blah"
        req = self._get_request()
        req.method = 'PUT'
        req.body = jsonutils.dumps(data)
        req.headers["content-type"] = "application/json"

        # test for long key
        data = {"metadata": {"a" * 260: "value1"}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex_large,
                          self.controller.update_all, req, self.uuid,
                          body=data)

        # test for long value
        data = {"metadata": {"key": "v" * 260}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex_large,
                          self.controller.update_all, req, self.uuid,
                          body=data)

        # test for empty key.
        data = {"metadata": {"": "value1"}}
        req.body = jsonutils.dumps(data)
        self.assertRaises(self.validation_ex,
                          self.controller.update_all, req, self.uuid,
                          body=data)


class ServerMetaDataTestV2(ServerMetaDataTestV21):
    validation_ex = webob.exc.HTTPBadRequest
    validation_ex_large = webob.exc.HTTPRequestEntityTooLarge

    def _set_up_resources(self):
        self.controller = server_metadata_v2.Controller()
        self.uuid = str(uuid.uuid4())
        self.url = '/v1.1/fake/servers/%s/metadata' % self.uuid

    def _get_request(self, param_url=''):
        return fakes.HTTPRequest.blank(self.url + param_url)


class BadStateServerMetaDataTestV21(test.TestCase):

    def setUp(self):
        super(BadStateServerMetaDataTestV21, self).setUp()
        fakes.stub_out_key_pair_funcs(self.stubs)
        self.stubs.Set(nova.db, 'instance_metadata_get',
                       return_server_metadata)
        self.stubs.Set(compute_rpcapi.ComputeAPI, 'change_instance_metadata',
                       fake_change_instance_metadata)
        self.stubs.Set(nova.db, 'instance_get', self._return_server_in_build)
        self.stubs.Set(nova.db, 'instance_get_by_uuid',
                self._return_server_in_build_by_uuid)
        self.stubs.Set(nova.db, 'instance_metadata_delete',
                       delete_server_metadata)
        self._set_up_resources()

    def _set_up_resources(self):
        self.controller = server_metadata_v21.ServerMetadataController()
        self.uuid = str(uuid.uuid4())
        self.url = '/fake/servers/%s/metadata' % self.uuid

    def _get_request(self, param_url=''):
        return fakes.HTTPRequestV3.blank(self.url + param_url)

    def test_invalid_state_on_delete(self):
        req = self._get_request('/key2')
        req.method = 'DELETE'
        self.assertRaises(webob.exc.HTTPConflict, self.controller.delete,
                          req, self.uuid, 'key2')

    def test_invalid_state_on_update_metadata(self):
        self.stubs.Set(nova.db, 'instance_metadata_update',
                       return_create_instance_metadata)
        req = self._get_request()
        req.method = 'POST'
        req.content_type = 'application/json'
        expected = {
            'metadata': {
                'key1': 'updatedvalue',
                'key29': 'newkey',
            }
        }
        req.body = jsonutils.dumps(expected)
        self.assertRaises(webob.exc.HTTPConflict, self.controller.update_all,
                req, self.uuid, body=expected)

    def _return_server_in_build(self, context, server_id,
                                columns_to_join=None):
        return fake_instance.fake_db_instance(
            **{'id': server_id,
               'uuid': '0cc3346e-9fef-4445-abe6-5d2b2690ec64',
               'name': 'fake',
               'locked': False,
               'vm_state': vm_states.BUILDING})

    def _return_server_in_build_by_uuid(self, context, server_uuid,
                                        columns_to_join=None, use_subordinate=False):
        return fake_instance.fake_db_instance(
            **{'id': 1,
               'uuid': '0cc3346e-9fef-4445-abe6-5d2b2690ec64',
               'name': 'fake',
               'locked': False,
               'vm_state': vm_states.BUILDING})

    @mock.patch.object(nova.compute.api.API, 'update_instance_metadata',
                       side_effect=exception.InstanceIsLocked(instance_uuid=0))
    def test_instance_lock_update_metadata(self, mock_update):
        req = self._get_request()
        req.method = 'POST'
        req.content_type = 'application/json'
        expected = {
            'metadata': {
                'keydummy': 'newkey',
            }
        }
        req.body = jsonutils.dumps(expected)
        self.assertRaises(webob.exc.HTTPConflict, self.controller.update_all,
                req, self.uuid, body=expected)


class BadStateServerMetaDataTestV2(BadStateServerMetaDataTestV21):
    def _set_up_resources(self):
        self.controller = server_metadata_v2.Controller()
        self.uuid = str(uuid.uuid4())
        self.url = '/v1.1/fake/servers/%s/metadata' % self.uuid

    def _get_request(self, param_url=''):
        return fakes.HTTPRequest.blank(self.url + param_url)
