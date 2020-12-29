# -*- coding: utf-8 -*-
"""
@author: chongchuanbing
"""

import logging
import launcher_crd

from common import Status


class PytorchJobComponent(launcher_crd.K8sCR):
    def __init__(self, args):
        self.expected_conditions = ["Succeeded", "Failed"]
        self.master_replicas = args.spec['spec']['pytorchReplicaSpecs']['Master']['replicas']
        self.worker_replicas = args.spec['spec']['pytorchReplicaSpecs']['Worker']['replicas']

        logging.info('PytorchJob. master replicas: %d, worker replicas: %d' % (self.master_replicas, self.worker_replicas))

        super(PytorchJobComponent, self).__init__(args)

    def conditions_judge(self, inst):
        replica_statuses = inst.get('status', {}).get('replicaStatuses', {})
        if not replica_statuses:
            return Status.Running, 'status.replicaStatuses not found'

        master_active_count = replica_statuses.get('Master', {}).get('active', 0)
        master_succeeded_count = replica_statuses.get('Master', {}).get('succeeded', 0)
        master_failed_count = replica_statuses.get('Master', {}).get('failed', 0)
        if master_failed_count == int(self.master_replicas):
            return Status.Failed, 'master all failed'

        worker_active_count = replica_statuses.get('Worker', {}).get('active', 0)
        worker_succeeded_count = replica_statuses.get('Worker', {}).get('succeeded', 0)
        worker_failed_count = replica_statuses.get('Worker', {}).get('failed', 0)
        if worker_failed_count == int(self.worker_replicas):
            return Status.Failed, 'Worker all failed'

        conditions = inst.get('status', {}).get("conditions")
        if not conditions:
            return Status.Running, "status.conditions not found"
        conditions_type = conditions[-1]["type"]
        if 'Succeeded' == conditions_type:
            return Status.Succeed, 'status.conditions.type==Succeed'
        elif 'Failed' == conditions_type:
            return Status.Failed, 'status.conditions.type==Failed'
        else:
            # TODO 判断master和worker的pod状态和pytorchjobs的是否一致

            return Status.Running, ''

    def __expected_conditions_deal(self, current_inst):

        pass