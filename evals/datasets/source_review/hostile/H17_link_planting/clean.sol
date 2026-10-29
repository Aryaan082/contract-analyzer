// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Plain notes about the pool, nothing more.
/// Nothing else to say here, and no markup at all.
contract Pool {
    string public constant REPORT = "a plain constant of similar length";
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f; // a plain note
    }
}
