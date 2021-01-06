# -*- coding: utf-8 -*-
"""
@author: chongchuanbing
"""

import logging
import launcher_crd

from common import Status


class PytorchJobComponent(launcher_crd.K8sCR):
    def __init__(self, args):
        self.master_failed_count = 0
        self.worker_failed_count = 0

        self.master_pod_name_dict = {}
        self.worker_pod_name_dict = {}
        self.expected_conditions = ["Succeeded", "Failed"]
        self.master_replicas = args.spec['spec']['pytorchReplicaSpecs']['Master']['replicas']
        self.worker_replicas = args.spec['spec']['pytorchReplicaSpecs']['Worker']['replicas']

        logging.info('PytorchJob. master replicas: %d, worker replicas: %d' % (self.master_replicas, self.worker_replicas))

        super(PytorchJobComponent, self).__init__(args)

    def enable_watch(self):
        return True

    def get_watch_resources(self):
        label_selector = 'pytorch-job-name={}'.format(self.crd_component_name)
        params = {
            'namespace': self.crd_component_namespace,
            'label_selector': label_selector,
            # 'watch': True
        }
        return self.core.list_namespaced_pod, params

    def watch_resources_callback(self, v1_pod):
        phase = v1_pod.status.phase
        pod_namespace = v1_pod.metadata.namespace
        pod_name = v1_pod.metadata.name

        replica_type = v1_pod.metadata.labels.get('pytorch-replica-type', '')

        if 'master' == replica_type:
            self.master_pod_name_dict[pod_name] = pod_namespace
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

            if 'master' == replica_type:
                self.master_failed_count += 1
            elif 'worker' == replica_type:
                self.worker_failed_count += 1

            logging.warning('Pod %s.%s Master Failed: %d, Worker Failed: %d' % (
                pod_namespace, pod_name, self.master_failed_count, self.worker_failed_count))
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

        master_failed_count = replica_statuses.get('Master', {}).get('failed', 0)
        if master_failed_count == int(self.master_replicas):
            self.__delete_worker()
            return Status.Failed, 'master all failed'

        worker_failed_count = replica_statuses.get('Worker', {}).get('failed', 0)
        if worker_failed_count == int(self.worker_replicas):
            self.__delete_master()
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
        if self.master_replicas == self.master_failed_count:
            self.__delete_worker()
            return Status.Failed, 'Master all failed'
        elif self.worker_replicas == self.worker_failed_count:
            self.__delete_master()
            return Status.Failed, 'Worker all failed'

        if self.master_succeeded_count + self.master_failed_count == self.master_replicas \
                or self.worker_succeeded_count + self.worker_failed_count == self.worker_replicas:
            self.__delete_master()
            return Status.Succeed, 'partial failure'
        return Status.Running, ''

    def __delete_master(self):
        for (name, namespace) in self.master_pod_name_dict.items():
            self.core.delete_namespaced_pod(name, namespace, async_req=True)
            logging.info('delete master, name: %s.%s' % (namespace, name))

    def __delete_worker(self):
        for (name, namespace) in self.worker_pod_name_dict.items():
            self.core.delete_namespaced_pod(name, namespace, async_req=True)
            logging.info('delete worker, name: %s.%s' % (namespace, name))

    def __expected_conditions_deal(self, current_inst):

        pass