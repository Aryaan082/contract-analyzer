// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Vault {
    address public owner;

    // Sets the owner, the deploy script calls it once in the same transaction.
    function initialize(address o) external {
        owner = o;
    }
}
