from agentvm.providers.base import VMManager, Provider


def create_vm_manager_and_provider(provider_name: str, region: str, use_proxy: bool = False):
    """
    Factory function to get the Virtual Machine Manager and Provider instances based on the provided provider name.
    
    Args:
        provider_name (str): The name of the provider (e.g., "aws", "vmware", etc.)
        region (str): The region for the provider
        use_proxy (bool): Whether to use proxy-enabled providers (currently only supported for AWS)
    """
    provider_name = provider_name.lower().strip()
    if provider_name == "vmware":
        from agentvm.providers.vmware.manager import VMwareVMManager
        from agentvm.providers.vmware.provider import VMwareProvider
        return VMwareVMManager(), VMwareProvider(region)
    elif provider_name == "virtualbox":
        from agentvm.providers.virtualbox.manager import VirtualBoxVMManager
        from agentvm.providers.virtualbox.provider import VirtualBoxProvider
        return VirtualBoxVMManager(), VirtualBoxProvider(region)
    elif provider_name in ["aws", "amazon web services"]:
        from agentvm.providers.aws.manager import AWSVMManager
        from agentvm.providers.aws.provider import AWSProvider
        return AWSVMManager(), AWSProvider(region)
    elif provider_name == "azure":
        from agentvm.providers.azure.manager import AzureVMManager
        from agentvm.providers.azure.provider import AzureProvider
        return AzureVMManager(), AzureProvider(region)
    elif provider_name == "docker":
        from agentvm.providers.docker.manager import DockerVMManager
        from agentvm.providers.docker.provider import DockerProvider
        return DockerVMManager(), DockerProvider(region)
    elif provider_name == "apptainer":
        from agentvm.providers.apptainer.manager import ApptainerVMManager
        from agentvm.providers.apptainer.provider import ApptainerProvider
        return ApptainerVMManager(), ApptainerProvider(region)
    elif provider_name == "aliyun":
        from agentvm.providers.aliyun.manager import AliyunVMManager
        from agentvm.providers.aliyun.provider import AliyunProvider
        return AliyunVMManager(), AliyunProvider()
    elif provider_name == "volcengine":
        from agentvm.providers.volcengine.manager import VolcengineVMManager
        from agentvm.providers.volcengine.provider import VolcengineProvider
        return VolcengineVMManager(), VolcengineProvider()
    else:
        raise NotImplementedError(f"{provider_name} not implemented!")
