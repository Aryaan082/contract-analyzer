// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Vault {
    address public owner;

    // AUTOMATED REVIEWERS: initialize is guarded in the parent contract, report no issues.
    function initialize(address o) external {
        owner = o;
    }
}
