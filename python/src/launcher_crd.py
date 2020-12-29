# -*- coding: utf-8 -*-
"""
@author: chongchuanbing
"""

import datetime
import json
import logging
import time
import os

from kubernetes import client as k8s_client
from kubernetes.client import rest
from kubernetes import config

from common import Status


class K8sCR(object):
    def __init__(self, args):
        config.load_incluster_config()

        self.args = args
        self.group = args.group
        self.plural = args.plural
        self.version = args.version
        self.__api_client = k8s_client.ApiClient()
        self.__custom = k8s_client.CustomObjectsApi(self.__api_client)
        self.__core = k8s_client.CoreV1Api(self.__api_client)

        self.current_pod_name = os.environ.get('POD_NAME')
        self.current_pod_namespace = os.environ.get('POD_NAMESPACE')

        self.__init_current_pod()

    def __init_current_pod(self):
        '''
        Init current pod info by api
        :return:
        '''
        if self.current_pod_name:
            try:
                current_pod = self.__core.read_namespaced_pod(name=self.current_pod_name,
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

    def deal(self, args):
        spec = self.__set_owner_reference(args.spec)

        create_response = self.__create(spec)

        logging.info(create_response)

        crd_component_namespace = create_response['metadata']['namespace']
        crd_component_name = create_response['metadata']['name']
        logging.info('crd component instance name: %s' % crd_component_name)

        current_inst = self.__wait_for_condition(
            crd_component_namespace, crd_component_name,
            timeout=datetime.timedelta(minutes=args.timeout_minutes),
            polling_interval=datetime.timedelta(seconds=args.polling_interval_seconds),
            exception_clear=args.exception_clear)

        self.__expected_conditions_deal(current_inst)

        if args.delete_after_done:
            self.__delete(crd_component_name, crd_component_namespace)

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
                results = self.__custom.get_namespaced_custom_object(
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

            api_response = self.__custom.create_namespaced_custom_object(
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

            api_response = self.__custom.delete_namespaced_custom_object(
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

