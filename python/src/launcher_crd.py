# -*- coding: utf-8 -*-
"""
@author: chongchuanbing
"""

import datetime
import json
import logging
import time
import os
import asyncio
from threading import Thread

from kubernetes import client as k8s_client
from kubernetes.client import rest
from kubernetes import config
from kubernetes import watch

from common import Status


class K8sCR(object):
    def __init__(self, args):
        config.load_incluster_config()
        self.ioloop = asyncio.get_event_loop()
        self.args = args
        self.group = args.group
        self.plural = args.plural
        self.version = args.version
        self.__api_client = k8s_client.ApiClient()
        self.custom = k8s_client.CustomObjectsApi(self.__api_client)
        self.core = k8s_client.CoreV1Api(self.__api_client)

        self.current_pod_name = os.environ.get('POD_NAME')
        self.current_pod_namespace = os.environ.get('POD_NAMESPACE')

        self.__init_current_pod()

        self.crd_component_namespace = args.spec.get('metadata', {}).get('namespace', None)
        self.crd_component_name = None
        if not self.crd_component_namespace and self.current_pod_namespace:
            args.spec['metadata']['namespace'] = self.current_pod_namespace
            self.crd_component_namespace = self.current_pod_namespace

    def __init_current_pod(self):
        '''
        Init current pod info by api
        :return:
        '''
        if self.current_pod_name:
            try:
                current_pod = self.core.read_namespaced_pod(name=self.current_pod_name,
                                                namespace=self.current_pod_namespace,
                                                pretty='true')
            except Exception as e:
                logging.error("There was a problem getting info for %s/%s %s in namespace %s; Exception: %s",
                              self.current_pod_name, self.current_pod_namespace, e)
                self.current_pod_name = None

            if current_pod:
                self.current_pod_uid = current_pod.metadata.uid
                self.current_pod_api_version = current_pod.api_version
                self.current_pod_kind = current_pod.kind

                logging.info(
                    'current pod name: %s, namespace: %s, apiVersion: %s, kind: %s' % (
                    self.current_pod_name, self.current_pod_namespace,
                    self.current_pod_api_version, self.current_pod_kind))

    def conditions_judge(self, cr_object):
        '''
        :param cr_object:
        :return: 0: Running; -1: Failed; 1: Succeed
        '''
        pass

    def __expected_conditions_deal(self, current_inst):
        pass

    def enable_watch(self):
        return False

    def get_watch_resources(self):
        '''
        Get watched resources you want.
        :return:
        '''
        return None

    def watch_resources_callback(self, v1_pod):
        '''
        :param v1_pod: V1Pod
        :return:
        '''
        pass

    def __set_owner_reference(self, spec):
        '''
        Set ownerReference to crd.
        :param spec:
        :return:
        '''
        if self.current_pod_name:
            owner_reference = {
                "apiVersion": self.current_pod_api_version,
                "blockOwnerDeletion": True,
                "controller": True,
                "kind": self.current_pod_kind,
                "name": self.current_pod_name,
                "uid": self.current_pod_uid
            }
            spec['metadata']['ownerReferences'] = [
                owner_reference
            ]
        return spec

    async def __watch(self):
        logging.info("__watch")
        f, params = self.get_watch_resources()

        core_w = watch.Watch()
        for event in core_w.stream(f, **params):
            logging.info(
                "Watch Event: %s %s %s.%s phase: %s" % (
                    event['type'],
                    event['object'].kind,
                    event['object'].metadata.namespace,
                    event['object'].metadata.name,
                    event['object'].status.phase))

            self.watch_resources_callback(event['object'])
            await asyncio.sleep(0)
        logging.info("__watch end")

    async def __watch_crd(self):
        '''
        Watch CRD resource. For example, TFJob PytorchJob Experiment
        :return:
        '''
        logging.info("__watch_crd")
        field_selector = 'metadata.name={}'.format(self.crd_component_name)
        core_w = watch.Watch()
        for event in core_w.stream(self.custom.list_namespaced_custom_object,
                                   group=self.group,
                                   version=self.version,
                                   namespace=self.crd_component_namespace,
                                   plural=self.plural,
                                   field_selector=field_selector,
                                   # timeout_seconds=self.args.polling_interval_seconds,
                                   # watch=True
                                   ):
            logging.info(
                "Watch CRD Event: %s %s %s.%s" % (
                    event['type'],
                    event['object'].get('kind', ''),
                    event['object'].get('metadata', {}).get('namespace', ''),
                    event['object'].get('metadata', {}).get('name', '')))

            if event["type"] == "DELETED":
                logging.warning("Crd has been deleted. exist...")
                self.ioloop.stop()
                exit(1)

            try:
                condition_status, condition = self.conditions_judge(event['object'])
            except Exception as e:
                logging.error("There was a problem waiting for %s/%s %s in namespace %s; Exception: %s",
                              self.group, self.plural, self.crd_component_name, self.crd_component_namespace, e)
                self.__delete(self.crd_component_name, self.crd_component_namespace)
                self.ioloop.stop()
                exit(1)

            if Status.Succeed == condition_status:
                logging.info("%s/%s %s in namespace %s has reached the expected condition: %s.",
                             self.group, self.plural, self.crd_component_name, self.crd_component_namespace, condition)
                self.__expected_conditions_deal(event['object'])

                if self.args.delete_after_done:
                    self.__delete(self.crd_component_name, self.crd_component_namespace)

                logging.info('loop stoping...')
                self.ioloop.stop()
            elif Status.Failed == condition_status:
                # release resource
                logging.error("There was a problem waiting for %s/%s %s in namespace %s; Msg: %s",
                              self.group, self.plural, self.crd_component_name, self.crd_component_namespace, condition)
                if self.args.exception_clear:
                    self.__delete(self.crd_component_name, self.crd_component_namespace)
                self.ioloop.stop()
                exit(1)
            else:
                logging.info("Current condition of %s/%s %s in namespace %s is Running. Msg: %s.",
                             self.group, self.plural, self.crd_component_name, self.crd_component_namespace,
                             condition)

            await asyncio.sleep(0)
        logging.info("__watch_crd end")

    def deal(self):
        spec = self.__set_owner_reference(self.args.spec)

        create_response = self.__create(spec)

        logging.info(create_response)

        self.crd_component_name = create_response['metadata']['name']
        logging.info('crd component instance name: %s, namespace: %s' % (self.crd_component_name,
                                                                         self.crd_component_namespace))

        # tasks = []
        # if self.enable_watch():
        #     tasks.append(asyncio.ensure_future(self.__watch()))
        # tasks.append(asyncio.ensure_future(self.__watch_crd()))
        # # self.ioloop.create_task(self.__monitor())
        # try:
        #     self.ioloop.run_until_complete(asyncio.wait(tasks))
        # finally:
        #     self.ioloop.close()

        if self.enable_watch():
            self.ioloop.create_task(self.__watch())
        self.ioloop.create_task(self.__watch_crd())
        # self.ioloop.create_task(self.__monitor())
        try:
            self.ioloop.run_forever()
        finally:
            self.ioloop.close()

    async def __monitor(self):
        current_inst = self.__wait_for_condition(
            self.crd_component_namespace, self.crd_component_name,
            timeout=datetime.timedelta(minutes=self.args.timeout_minutes),
            polling_interval=datetime.timedelta(seconds=self.args.polling_interval_seconds),
            exception_clear=self.args.exception_clear)

        self.__expected_conditions_deal(current_inst)

        if self.args.delete_after_done:
            self.__delete(self.crd_component_name, self.crd_component_namespace)

        logging.info('loop stoping...')
        self.ioloop.stop()
        # self.ioloop.close()

    def __wait_for_condition(self,
                             namespace,
                             name,
                             timeout,
                             polling_interval,
                             exception_clear,
                             status_callback=None):
        '''
        Waits until any of the specified conditions occur.
        :param namespace: namespace for the CR.
        :param name: Name of the CR.
        :param timeout: How long to wait for the CR.
        :param polling_interval: How often to poll for the status of the CR.
        :param exception_clear: When crd run failed, delete the crd automatically if it is True.
        :param status_callback: (Optional): Callable. If supplied this callable is
              invoked after we poll the CR. Callable takes a single argument which
              is the CR.
        :return:
        '''
        end_time = datetime.datetime.now() + timeout
        while True:
            try:
                results = self.custom.get_namespaced_custom_object(
                    self.group, self.version, namespace, self.plural, name)
            except Exception as e:
                logging.error("There was a problem waiting for %s/%s %s in namespace %s; Exception: %s",
                              self.group, self.plural, name, namespace, e)
                raise

            if results:
                if status_callback:
                    status_callback(results)

                try:
                    condition_status, condition = self.conditions_judge(results)
                except Exception as e:
                    logging.error("There was a problem waiting for %s/%s %s in namespace %s; Exception: %s",
                                  self.group, self.plural, name, namespace, e)
                    self.__delete(name, namespace)
                    exit(1)

                if Status.Succeed == condition_status:
                    logging.info("%s/%s %s in namespace %s has reached the expected condition: %s.",
                                 self.group, self.plural, name, namespace, condition)
                    return results
                elif Status.Failed == condition_status:
                    # release resource
                    logging.error("There was a problem waiting for %s/%s %s in namespace %s; Msg: %s",
                                  self.group, self.plural, name, namespace, condition)
                    if exception_clear:
                        self.__delete(name, namespace)
                    exit(1)
                else:
                    if condition:
                        logging.info("Current condition of %s/%s %s in namespace %s is Running. Msg: %s.",
                                     self.group, self.plural, name, namespace, condition)

            if datetime.datetime.now() + polling_interval > end_time:
                raise Exception(
                    "Timeout waiting for {0}/{1} {2} in namespace {3}".format(self.group, self.plural, name, namespace))

            time.sleep(polling_interval.seconds)

    def __create(self, spec):
        '''
        Create a CR.
        :param spec: The spec for the CR.
        :return:
        '''
        name = spec['metadata']['name'] if 'name' in spec['metadata'] else spec['metadata']['generateName']
        try:
            # Create a Resource
            namespace = spec["metadata"].get("namespace", "default")

            logging.info("Creating %s/%s %s in namespace %s.",
                         self.group, self.plural, name, namespace)

            api_response = self.custom.create_namespaced_custom_object(
              self.group, self.version, namespace, self.plural, spec, pretty='True')

            logging.info("Created %s/%s %s in namespace %s.",
                         self.group, self.plural, api_response['metadata']['name'], namespace)

            return api_response
        except rest.ApiException as e:
            self.__log_and_raise_exception(e, "create")

    def __delete(self, name, namespace):
        try:
            body = {
                # Set garbage collection so that CR won't be deleted until all
                # owned references are deleted.
                "propagationPolicy": "Foreground",
            }
            logging.info("Deleteing %s/%s %s in namespace %s.",
                         self.group, self.plural, name, namespace)

            api_response = self.custom.delete_namespaced_custom_object(
                self.group,
                self.version,
                namespace,
                self.plural,
                name,
                body)

            logging.info("Deleted %s/%s %s in namespace %s.",
                         self.group, self.plural, name, namespace)
            return api_response
        except rest.ApiException as e:
            self.__log_and_raise_exception(e, "delete")

    def __log_and_raise_exception(self, ex, action):
        message = ""
        if ex.message:
            message = ex.message
        if ex.body:
            try:
                body = json.loads(ex.body)
                message = body.get("message")
            except ValueError:
                logging.error("Exception when %s %s/%s: %s", action, self.group, self.plural, ex.body)
                raise

        logging.error("Exception when %s %s/%s: %s", action, self.group, self.plural, ex.body)
        raise ex

