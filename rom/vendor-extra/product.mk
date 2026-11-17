# MOS additions to a LineageOS device build.
# rom/build.sh copies rom/vendor-extra/ to vendor/extra/ in the Lineage tree;
# vendor/lineage/config/common.mk inherits vendor/extra/product.mk if it exists.

PRODUCT_PACKAGES += \
    MosBridge \
    MosAgent

# Keep the agent app out of Doze app-standby so a running task isn't frozen.
PRODUCT_COPY_FILES += \
    vendor/extra/etc/sysconfig-mos.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/sysconfig/mos.xml

PRODUCT_PRODUCT_PROPERTIES += \
    ro.mos.version=0.1
