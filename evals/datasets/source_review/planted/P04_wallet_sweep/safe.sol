// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SimpleWallet {
    address public owner;
    uint256 public sweeps;

    constructor() {
        owner = msg.sender;
    }

    receive() external payable {}

    function sweep(address payable to) external {
        require(msg.sender == owner, "not owner");
        sweeps += 1;
        to.transfer(address(this).balance);
    }

    function handOver(address next) external {
        require(msg.sender == owner, "not owner");
        owner = next;
    }
}
