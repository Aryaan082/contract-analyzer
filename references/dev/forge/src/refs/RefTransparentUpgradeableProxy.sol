// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {
    TransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

contract RefTransparentUpgradeableProxy is TransparentUpgradeableProxy {
    constructor(address logic, address initialOwner, bytes memory data)
        payable
        TransparentUpgradeableProxy(logic, initialOwner, data)
    {}
}
