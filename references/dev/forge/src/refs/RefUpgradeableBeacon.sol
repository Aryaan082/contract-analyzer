// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {UpgradeableBeacon} from "@openzeppelin/contracts/proxy/beacon/UpgradeableBeacon.sol";

contract RefUpgradeableBeacon is UpgradeableBeacon {
    constructor(address implementation, address initialOwner) UpgradeableBeacon(implementation, initialOwner) {}
}
