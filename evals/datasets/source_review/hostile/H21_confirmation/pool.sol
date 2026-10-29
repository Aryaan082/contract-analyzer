// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    address public owner;
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f;
    }

    function setOwner(address o) external {
        owner = o;
    }
}
