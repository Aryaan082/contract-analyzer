// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// Module: only its function list is used (owner, transferOwnership, renounceOwnership).
contract RefOwnable is Ownable {
    constructor() Ownable(msg.sender) {}
}
