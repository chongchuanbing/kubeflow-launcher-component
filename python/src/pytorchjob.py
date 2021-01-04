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
        if 'Failed' != phase:
            return Status.Running, ''

        pod_namespace = v1_pod.metadata.namespace
        pod_name = v1_pod.metadata.name

        pod_logs = self.core.read_namespaced_pod_log(name=pod_name,
                                                     namespace=pod_namespace,
                                                     pretty="True",
                                                     tail_lines=self.args.tail_lines)
        logging.warning('Pod %s.%s Failed. Log: \n%s' % (pod_namespace, pod_name, pod_logs))

        pytorch_replica_type = v1_pod.metadata.labels.get('pytorch-replica-type', '')
        if 'master' == pytorch_replica_type:
            self.master_failed_count += 1
        elif 'worker' == pytorch_replica_type:
            self.worker_failed_count += 1

        logging.warning('Pod %s.%s Master Failed: %d, Worker Failed: %d' % (
            pod_namespace, pod_name, self.master_failed_count, self.worker_failed_count))

        if self.master_replicas == self.master_failed_count:
            return Status.Failed, 'Master all failed'
        elif self.worker_replicas == self.worker_failed_count:
            return Status.Failed, 'Worker all failed'

        return Status.Running, ''

    def conditions_judge(self, inst):
        replica_statuses = inst.get('status', {}).get('replicaStatuses', {})
        if not replica_statuses:
            return Status.Running, 'status.replicaStatuses not found'

        master_failed_count = replica_statuses.get('Master', {}).get('failed', 0)
        if master_failed_count == int(self.master_replicas):
            return Status.Failed, 'master all failed'

        worker_failed_count = replica_statuses.get('Worker', {}).get('failed', 0)
        if worker_failed_count == int(self.worker_replicas):
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
            # 判断master和worker的pod状态和pytorchjobs的是否一致
            if self.master_replicas == self.master_failed_count:
                return Status.Failed, 'Master all failed'
            elif self.worker_replicas == self.worker_failed_count:
                return Status.Failed, 'Worker all failed'
            return Status.Running, ''

    def __expected_conditions_deal(self, current_inst):

        pass