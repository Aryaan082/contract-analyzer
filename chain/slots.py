"""Storage slots where proxy patterns keep their pointers.

EIP-1967 slots are keccak256("eip1967.proxy.<name>") - 1: the - 1 stops anyone from knowing a
preimage, so they cannot collide with ordinary Solidity storage. The legacy OpenZeppelin slots
(older "zos" proxies, such as USDC's) are keccak256 of the name with no - 1. Recognising legacy
proxies is out of scope for resolve_proxy v1; the slots are here so the inspector can show them.
"""

EIP1967_IMPLEMENTATION = 0x360894A13BA1A3210667C828492DB98DCA3E2076CC3735A920A3CA505D382BBC
EIP1967_ADMIN = 0xB53127684A568B3173AE13B9F8A6016E243E63B6E8EE1178D6A717850B5D6103
EIP1967_BEACON = 0xA3F0AD74E5423AEBFD80D3EF4346578335A9A72AEAEE59FF6CB3582B35133D50
LEGACY_OZ_IMPLEMENTATION = 0x7050C9E0F4CA769C69BD3A8EF740BC37934F8E2C036E5A723FD8EE048ED3F8C3
LEGACY_OZ_ADMIN = 0x10D6A54A4754C8869D6886B5F5D7FBFA5B4522237EA5C60D11BC4E7A1FF9390B

PRESETS = {
    "eip1967-implementation": EIP1967_IMPLEMENTATION,
    "eip1967-admin": EIP1967_ADMIN,
    "eip1967-beacon": EIP1967_BEACON,
    "legacy-oz-implementation": LEGACY_OZ_IMPLEMENTATION,
    "legacy-oz-admin": LEGACY_OZ_ADMIN,
}
