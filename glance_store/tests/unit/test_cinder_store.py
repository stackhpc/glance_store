# Copyright 2013 OpenStack Foundation
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

import errno
import io
from unittest import mock

import six
import sys
import uuid

from oslo_utils import units

from glance_store import exceptions
from glance_store import location
from glance_store.tests import base
from glance_store.tests.unit import test_cinder_base
from glance_store.tests.unit import test_store_capabilities

sys.modules['glance_store.common.fs_mount'] = mock.Mock()
from glance_store._drivers import cinder # noqa


class TestCinderStore(base.StoreBaseTest,
                      test_store_capabilities.TestStoreCapabilitiesChecking,
                      test_cinder_base.TestCinderStoreBase):

    def setUp(self):
        super(TestCinderStore, self).setUp()
        self.store = cinder.Store(self.conf)
        self.store.configure()
        self.register_store_schemes(self.store, 'cinder')
        self.store.READ_CHUNKSIZE = 4096
        self.store.WRITE_CHUNKSIZE = 4096

        fake_sc = [{u'endpoints': [{u'publicURL': u'http://foo/public_url'}],
                    u'endpoints_links': [],
                    u'name': u'cinder',
                    u'type': u'volumev3'}]
        self.context = mock.MagicMock(service_catalog=fake_sc,
                                      user_id='fake_user',
                                      auth_token='fake_token',
                                      project_id='fake_project')
        self.hash_algo = 'sha256'
        cinder._reset_cinder_session()
        self.config(cinder_mount_point_base=None)

    def _test_get_cinderclient_with_user_overriden(self):
        self.config(cinder_store_user_name='test_user')
        self.config(cinder_store_password='test_password')
        self.config(cinder_store_project_name='test_project')
        self.config(cinder_store_auth_address='test_address')
        cc = self.store.get_cinderclient(self.context)
        self.assertEqual('test_project', cc.client.session.auth.project_name)
        self.assertEqual('Default', cc.client.session.auth.project_domain_name)
        return cc

    def test_get_cinderclient_with_user_overriden(self):
        self._test_get_cinderclient_with_user_overriden()

    def test_get_cinderclient_with_user_overriden_and_region(self):
        self.config(cinder_os_region_name='test_region')
        cc = self._test_get_cinderclient_with_user_overriden()
        self.assertEqual('test_region', cc.client.region_name)

    def test_open_cinder_volume_multipath_enabled(self):
        self.config(cinder_use_multipath=True)
        self._test_open_cinder_volume('wb', 'rw', None,
                                      multipath_supported=True)

    def test_open_cinder_volume_multipath_disabled(self):
        self.config(cinder_use_multipath=False)
        self._test_open_cinder_volume('wb', 'rw', None,
                                      multipath_supported=False)

    def test_open_cinder_volume_enforce_multipath(self):
        self.config(cinder_use_multipath=True)
        self.config(cinder_enforce_multipath=True)
        self._test_open_cinder_volume('wb', 'rw', None,
                                      multipath_supported=True,
                                      enforce_multipath=True)

    def test_cinder_configure_add(self):
        self.assertRaises(exceptions.BadStoreConfiguration,
                          self.store._check_context, None)

        self.assertRaises(exceptions.BadStoreConfiguration,
                          self.store._check_context,
                          mock.MagicMock(service_catalog=None))

        self.store._check_context(mock.MagicMock(service_catalog='fake'))

    def test_cinder_get(self):
        self._test_cinder_get()

    def test_cinder_get_size(self):
        self._test_cinder_get_size()

    def test_cinder_get_size_with_metadata(self):
        self._test_cinder_get_size_with_metadata()

    def test_cinder_add(self):
        fake_volume = mock.MagicMock(id=str(uuid.uuid4()),
                                     status='available',
                                     size=1)
        volume_file = six.BytesIO()
        self._test_cinder_add(fake_volume, volume_file)

    def test_cinder_add_with_verifier(self):
        fake_volume = mock.MagicMock(id=str(uuid.uuid4()),
                                     status='available',
                                     size=1)
        volume_file = six.BytesIO()
        verifier = mock.MagicMock()
        self._test_cinder_add(fake_volume, volume_file, 1, verifier)
        verifier.update.assert_called_with(b"*" * units.Ki)

    def test_cinder_add_volume_full(self):
        e = IOError()
        volume_file = six.BytesIO()
        e.errno = errno.ENOSPC
        fake_volume = mock.MagicMock(id=str(uuid.uuid4()),
                                     status='available',
                                     size=1)
        with mock.patch.object(volume_file, 'write', side_effect=e):
            self.assertRaises(exceptions.StorageFull,
                              self._test_cinder_add, fake_volume, volume_file)
        fake_volume.delete.assert_called_once_with()

    def test_cinder_add_fail_resize(self):
        volume_file = io.BytesIO()
        fake_volume = mock.MagicMock(id=str(uuid.uuid4()),
                                     status='available',
                                     size=1)
        self.assertRaises(exceptions.BackendException,
                          self._test_cinder_add, fake_volume, volume_file,
                          fail_resize=True)
        fake_volume.delete.assert_called_once()

    def test_cinder_delete(self):
        fake_client = mock.MagicMock(auth_token=None, management_url=None)
        fake_volume_uuid = str(uuid.uuid4())
        fake_volumes = mock.MagicMock(delete=mock.Mock())

        with mock.patch.object(cinder.Store, 'get_cinderclient') as mocked_cc:
            mocked_cc.return_value = mock.MagicMock(client=fake_client,
                                                    volumes=fake_volumes)

            uri = 'cinder://%s' % fake_volume_uuid
            loc = location.get_location_from_uri(uri, conf=self.conf)
            self.store.delete(loc, context=self.context)
            fake_volumes.delete.assert_called_once_with(fake_volume_uuid)

    def test_set_url_prefix(self):
        self.assertEqual('cinder://', self.store._url_prefix)

    def test_configure_add_valid_type(self):
        self.config(cinder_volume_type='some_type')
        self._test_configure_add_valid_type()

    def test_configure_add_invalid_type(self):
        # setting cinder_volume_type to non-existent value will log a
        # warning
        self.config(cinder_volume_type='some_random_type')
        self._test_configure_add_invalid_type()
