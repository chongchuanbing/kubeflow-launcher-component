# -*- coding: utf-8 -*-
"""
@author: chongchuanbing
"""

import logging
import json
import launcher_crd

from common import Status


class TFJobComponent(launcher_crd.K8sCR):
    def __init__(self, args):
        self.ps_failed_count = 0
        self.worker_failed_count = 0
        self.ps_succeeded_count = 0
        self.worker_succeeded_count = 0
        self.ps_pod_name_dict = {}
        self.worker_pod_name_dict = {}
        self.expected_conditions = ["Succeeded", "Failed"]
        self.ps_replicas = args.spec['spec']['tfReplicaSpecs']['PS']['replicas']
        self.worker_replicas = args.spec['spec']['tfReplicaSpecs']['Worker']['replicas']

        logging.info('TFJob. ps replicas: %d, worker replicas: %d' % (self.ps_replicas, self.worker_replicas))

        super(TFJobComponent, self).__init__(args)

    def enable_watch(self):
        return True

    def get_watch_resources(self):
        label_selector = 'tf-job-name={}'.format(self.crd_component_name)
        params = {
            'namespace': self.crd_component_namespace,
            'label_selector': label_selector,
            # 'watch': True
        }
        return self.core.list_namespaced_pod, params

    def watch_resources_callback(self, v1_pod):
        phase = v1_pod.status.phase
        replica_type = v1_pod.metadata.labels.get('tf-replica-type', '')
        pod_namespace = v1_pod.metadata.namespace
        pod_name = v1_pod.metadata.name

        if 'ps' == replica_type:
            self.ps_pod_name_dict[pod_name] = pod_namespace
        elif 'worker' == replica_type:
            self.worker_pod_name_dict[pod_name] = pod_namespace

        if 'Failed' == phase:
            reason = v1_pod.status.reason

            pod_logs = self.core.read_namespaced_pod_log(name=pod_name,
                                                         namespace=pod_namespace,
                                                         pretty="True",
                                                         tail_lines=self.args.tail_lines)
            logging.warning('Pod %s.%s Failed, replica_type: %s, Reason: %s. Log: \n%s' % (pod_namespace,
                                                                                           pod_name, replica_type,
                                                                                           reason,
                                                                                           pod_logs))

            if 'ps' == replica_type:
                self.ps_failed_count += 1
            elif 'worker' == replica_type:
                self.worker_failed_count += 1

            logging.warning('Pod %s.%s Ps Failed: %d, Worker Failed: %d' % (
                pod_namespace, pod_name, self.ps_failed_count, self.worker_failed_count))
        elif 'Succeeded' == phase:
            if 'ps' == replica_type:
                self.ps_succeeded_count += 1
            elif 'worker' == replica_type:
                self.worker_succeeded_count += 1
            pass

        return self.__judge_status()

    def conditions_judge(self, inst):
        replica_statuses = inst.get('status', {}).get('replicaStatuses', {})
        if not replica_statuses:
            return Status.Running, 'status.replicaStatuses not found'

        ps_failed_count = replica_statuses.get('PS', {}).get('failed', 0)
        if ps_failed_count == int(self.ps_replicas):
            self.__delete_worker()
            return Status.Failed, 'Ps all failed'

        worker_failed_count = replica_statuses.get('Worker', {}).get('failed', 0)
        if worker_failed_count == int(self.worker_replicas):
            self.__delete_ps()
            return Status.Failed, 'Worker all failed'

        conditions = inst.get('status', {}).get("conditions")
        if not conditions:
            return Status.Running, "status.conditions not found"
        conditions_type = conditions[-1]["type"]
        if 'Succeeded' == conditions_type:
            return Status.Succeed, 'status.conditions.type==Succeed'
        # elif 'Failed' == conditions_type:
        #     return Status.Failed, 'status.conditions.type==Failed'
        else:
            return self.__judge_status()

    def __judge_status(self):
        if self.ps_replicas == self.ps_failed_count:
            self.__delete_worker()
            return Status.Failed, 'Ps all failed'
        elif self.worker_replicas == self.worker_failed_count:
            self.__delete_ps()
            return Status.Failed, 'Worker all failed'

        if self.ps_succeeded_count + self.ps_failed_count == self.ps_replicas \
                or self.worker_succeeded_count + self.worker_failed_count == self.worker_replicas:
            self.__delete_ps()
            return Status.Succeed, 'partial failure'
        return Status.Running, ''

    def __delete_ps(self):
        for (name, namespace) in self.ps_pod_name_dict.items():
            self.core.delete_namespaced_pod(name, namespace, async_req=True)
            logging.info('delete ps, name: %s.%s' % (namespace, name))

    def __delete_worker(self):
        for (name, namespace) in self.worker_pod_name_dict.items():
            self.core.delete_namespaced_pod(name, namespace, async_req=True)
            logging.info('delete worker, name: %s.%s' % (namespace, name))

    def __expected_conditions_deal(self, current_inst):

        pass